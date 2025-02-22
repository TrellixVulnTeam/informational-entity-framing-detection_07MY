import torch
from torch import nn
from torch.autograd import Variable
from transformers.optimization import AdamW, get_linear_schedule_with_warmup
from torch.optim import lr_scheduler
from torch.utils.data import (DataLoader, SequentialSampler, RandomSampler, TensorDataset)
from lib.evaluate.Eval import my_eval
from transformers import BertModel, BertPreTrainedModel, BertForSequenceClassification
from lib.utils import format_runtime, format_checkpoint_filepath, get_torch_device
import os, time
import numpy as np

from torch.nn import CrossEntropyLoss, NLLLoss, MSELoss, Embedding, Dropout, Linear, Sigmoid, LSTM


class BahdanauAttention(nn.Module):
    """Implements Bahdanau (MLP) attention from: https://bastings.github.io/annotated_encoder_decoder/"""

    def __init__(self, hidden_size, key_size=None, query_size=None):
        super(BahdanauAttention, self).__init__()

        # We assume a bi-directional encoder so key_size is 2*hidden_size
        key_size = 2 * hidden_size if key_size is None else key_size
        query_size = hidden_size if query_size is None else query_size

        self.key_layer = nn.Linear(key_size, hidden_size, bias=False)
        self.query_layer = nn.Linear(query_size, hidden_size, bias=False)
        self.energy_layer = nn.Linear(hidden_size, 1, bias=False)

        # to store attention scores
        self.alphas = None

    def forward(self, query=None, proj_key=None, value=None, mask=None):
        assert mask is not None, "mask is required"

        # We first project the query (the decoder state).
        # The projected keys (the encoder states) were already pre-computated.
        query = self.query_layer(query)
        query = query.unsqueeze(1)

        # Calculate scores.
        scores = self.energy_layer(torch.tanh(query + proj_key))
        scores = scores.squeeze(2).unsqueeze(1)

        # Mask out invalid positions.
        # The mask marks valid positions so we invert it using `mask & 0`.
        scores.data.masked_fill_(mask == 0, -float('inf'))

        # Turn scores to probabilities.
        alphas = nn.functional.softmax(scores, dim=-1)
        self.alphas = alphas

        # The context vector is the weighted sum of the values.
        context = torch.bmm(alphas, value)

        # context shape: [B, 1, 2D], alphas shape: [B, 1, M]
        return context, alphas


class ContextAwareModel(nn.Module):
    """
    Model that applies BiLSTM and classification of hidden representation of token at target index.
    :param input_size: length of input sequences (= documents)
    :param hidden_size: size of hidden layer
    :param weights_matrix: matrix of embeddings of size vocab_size * embedding dimension
    """
    def __init__(self, input_size, hidden_size, bilstm_layers, weights_matrix, cam_type, device, context='article',
                 pos_dim=100, src_dim=100, pos_quartiles=4, nr_srcs=3):
        super(ContextAwareModel, self).__init__()

        self.input_size = input_size
        self.hidden_size = hidden_size # + pos_dim + src_dim
        self.bilstm_layers = bilstm_layers
        self.device = device

        self.weights_matrix = torch.tensor(weights_matrix, dtype=torch.float, device=self.device)
        self.embedding = Embedding.from_pretrained(self.weights_matrix)
        self.embedding_pos = Embedding(pos_quartiles, pos_dim) # 4=nr of quart
        self.embedding_src = Embedding(nr_srcs, src_dim)

        self.emb_size = weights_matrix.shape[1]

        self.lstm = LSTM(self.input_size, self.hidden_size, num_layers=self.bilstm_layers, bidirectional=True, dropout=0.2)
        self.lstm_art = LSTM(self.input_size, self.hidden_size, num_layers=self.bilstm_layers, bidirectional=True, dropout=0.2)
        self.lstm_cov1 = LSTM(self.input_size, self.hidden_size, num_layers=self.bilstm_layers, bidirectional=True, dropout=0.2)
        self.lstm_cov2 = LSTM(self.input_size, self.hidden_size, num_layers=self.bilstm_layers, bidirectional=True, dropout=0.2)
        self.attention = BahdanauAttention(self.hidden_size, key_size=self.hidden_size * 2, query_size=self.emb_size)
        self.dropout = Dropout(0.6)
        self.num_labels = 2
        self.pad_index = 0

        self.cam_type = cam_type
        self.context = context

        if self.cam_type == 'cam':
            self.context_rep_dim = self.hidden_size * 2 # + self.hidden_size * 2 + src_dim
        elif self.cam_type == 'cim':
            self.context_rep_dim = self.emb_size + self.hidden_size * 6
        elif self.cam_type == 'cim*':
            self.context_rep_dim = self.emb_size + self.hidden_size * 6 + src_dim
        elif self.cam_type == 'cim$':
            self.context_rep_dim = self.emb_size + self.hidden_size * 6 + pos_dim
        elif self.cam_type == 'cim#':
            self.context_rep_dim = self.emb_size + self.hidden_size * 6 + pos_dim + src_dim

        self.half_context_rep_dim = int(self.context_rep_dim*0.5)
        self.dense = nn.Linear(self.context_rep_dim, self.half_context_rep_dim)

        # self.rob_squeezer = nn.Linear(self.emb_size, self.hidden_size)

        if self.cam_type == 'cnm':
            self.classifier = Linear(self.emb_size, self.num_labels)
        else:
            #self.classifier = Linear(self.hidden_size * 2, 2)
            self.classifier = Linear(self.half_context_rep_dim, self.num_labels) # + self.emb_size + src_dim, 2) #

        self.sigm = Sigmoid()

    def forward(self, inputs):
        """
        Forward pass.
        :param input_tensor: batchsize * seq_length
        :param target_idx: batchsize, specifies which token is to be classified
        :return: sigmoid output of size batchsize
        """

        # inputs
        # token_ids, token_mask, contexts, positions = inputs
        token_ids, token_mask, article, cov1, cov2, positions, quartiles, srcs = inputs

        # shapes and sizes
        batch_size = inputs[0].shape[0]
        sen_len = token_ids.shape[1]
        doc_len = article.shape[1]
        seq_len = doc_len

        # init containers for outputs
        rep_dimension = self.emb_size if self.cam_type == 'cnm' else self.hidden_size * 2
        art_representations = torch.zeros(batch_size, seq_len, rep_dimension, device=self.device)

        #if self.context != 'article':
        #    cov1_representations = torch.zeros(batch_size, seq_len, rep_dimension, device=self.device)
        #    cov2_representations = torch.zeros(batch_size, seq_len, rep_dimension, device=self.device)

        target_sent_reps = torch.zeros(batch_size, self.emb_size, device=self.device)

        if self.cam_type == 'cnm':
            target_sent_reps = torch.zeros(batch_size, rep_dimension, device=self.device)
            for item, position in enumerate(positions):
                target_sent_reps[item] = self.embedding(article[item, position]).view(1, -1)

        else:
            for item, position in enumerate(positions):
                # target_hid = sentence_representations[item, position].view(1, -1)
                target_roberta = self.embedding(article[item, position]).view(1, -1)
                # target_sent_reps[item] = torch.cat((target_hid, target_roberta), dim=1)
                # if self.cam_type == 'cam':
                #    target_sent_reps[item] = target_hid
                #else:
                target_sent_reps[item] = target_roberta
                # target_sent_reps[item] = target_hid

            embedded_pos = self.embedding_pos(quartiles)
            embedded_src = self.embedding_src(srcs)

            # embedding article

            hidden = self.init_hidden(batch_size)
            for seq_idx in range(article.shape[0]):
                embedded_sentence = self.embedding(article[:, seq_idx]).view(1, batch_size, -1)
                lstm_input = embedded_sentence # torch.cat((embedded_sentence, embedded_src), dim=-1)
                #encoded, hidden = self.lstm_art(lstm_input, hidden)
                encoded, hidden = self.lstm(lstm_input, hidden)
                art_representations[:, seq_idx] = encoded
            final_article_reps = art_representations[:, -1, :]

            # embedding first coverage piece
            '''
            if self.context != 'article':
                hidden = self.init_hidden(batch_size)
                for seq_idx in range(article.shape[0]):
                    embedded_sentence = self.embedding(cov1[:, seq_idx]).view(1, batch_size, -1)
                    encoded, hidden = self.lstm_cov1(embedded_sentence, hidden)
                    cov1_representations[:, seq_idx] = encoded
                final_cov1_reps = cov1_representations[:, -1, :]
    
                hidden = self.init_hidden(batch_size)
                for seq_idx in range(article.shape[0]):
                    embedded_sentence = self.embedding(cov2[:, seq_idx]).view(1, batch_size, -1)
                    encoded, hidden = self.lstm_cov2(embedded_sentence, hidden)
                    cov2_representations[:, seq_idx] = encoded
                final_cov2_reps = cov2_representations[:, -1, :]
            '''
            
            #context_reps = torch.cat((final_article_reps, final_cov1_reps, final_cov2_reps), dim=-1)
            context_reps = final_article_reps

            # target_sent_reps = self.rob_squeezer(target_sent_reps)
            # query = target_sent_reps.unsqueeze(1)
            # proj_key = self.attention.key_layer(sentence_representations) #in tutorial: encoder_hidden
            # mask = (contexts != self.pad_index).unsqueeze(-2) #in tutorial: src
            #if self.cam_type == 'cim'
            if self.cam_type == 'cam+':
                context_and_target_rep = torch.cat((target_sent_reps, context_reps), dim=-1)
                # context_and_target_rep, attn_probs = self.attention(query=target_sent_reps, proj_key=proj_key,
                #                                         value=sentence_representations, mask=mask)
                # context_and_target_rep = torch.cat((context_and_target_rep, target_sent_reps), dim=-1)
            elif self.cam_type == 'cim*':
                # heavy_context_rep = torch.cat((target_sent_reps, sent_reps, embedded_pos, embedded_src), dim=-1)
                context_and_target_rep = torch.cat((target_sent_reps, context_reps, embedded_src), dim=-1)
            '''
            elif self.cam_type == 'cim$':
                # heavy_context_rep = torch.cat((target_sent_reps, sent_reps, embedded_pos, embedded_src), dim=-1)
                context_rep = torch.cat((target_sent_reps, final_sent_reps, embedded_pos), dim=-1)
                target_sent_reps = context_rep
            elif self.cam_type == 'cim#':
                # heavy_context_rep = torch.cat((target_sent_reps, sent_reps, embedded_pos, embedded_src), dim=-1)
                context_rep = torch.cat((target_sent_reps, final_sent_reps, embedded_src, embedded_pos), dim=-1)
                target_sent_reps = context_rep
            '''

        features = self.dropout(context_and_target_rep)
        features = self.dense(features)
        features = torch.tanh(features)
        features = self.dropout(features)
        logits = self.classifier(features)
        probs = self.sigm(logits)
        return logits, probs, target_sent_reps

    def init_hidden(self, batch_size):
        hidden = torch.zeros(self.bilstm_layers * 2, batch_size, self.hidden_size, device=self.device)
        cell = torch.zeros(self.bilstm_layers * 2, batch_size, self.hidden_size, device=self.device)
        return Variable(hidden), Variable(cell)


class ContextAwareClassifier():
    def __init__(self, emb_dim=768, hid_size=32, layers=1, weights_mat=None, tr_labs=None,
                 b_size=24, cp_dir='models/checkpoints/cam', lr=0.001, start_epoch=0, patience=3,
                 step=1, gamma=0.75, n_eps=10, cam_type='cam', context='article'):
        self.start_epoch = start_epoch
        self.cp_dir = cp_dir
        self.device, self.use_cuda = get_torch_device()

        self.emb_dim = emb_dim
        self.hidden_size = hid_size
        self.batch_size = b_size
        if cam_type == 'cam':
            self.criterion = CrossEntropyLoss(weight=torch.tensor([.20, .80], device=self.device), reduction='sum')  # could be made to depend on classweight which should be set on input
        else:
            self.criterion = CrossEntropyLoss(weight=torch.tensor([.25, .75], device=self.device), reduction='sum')  # could be made to depend on classweight which should be set on input

        # self.criterion = NLLLoss(weight=torch.tensor([.15, .85], device=self.device))
        # set criterion on input
        # n_pos = len([l for l in tr_labs if l == 1])
        # class_weight = 1 - (n_pos / len(tr_labs))
        # print(class_weight)
        # self.criterion = nn.BCEWithLogitsLoss(pos_weight=torch.tensor([.85], reduction='sum', dtype=torch.float, device=self.device))

        if start_epoch > 0:
            self.model = self.load_model()
        else:
            self.model = ContextAwareModel(input_size=self.emb_dim, hidden_size=self.hidden_size,
                                           bilstm_layers=layers, weights_matrix=weights_mat,
                                           device=self.device, cam_type=cam_type, context=context)
        self.model = self.model.to(self.device)
        if self.use_cuda: self.model.cuda()

        # empty now and set during or after training
        self.train_time = 0
        self.prev_val_f1 = 0
        self.cp_name = None  # depends on split type and current fold
        self.full_patience = patience
        self.current_patience = self.full_patience
        self.test_perf = []
        self.test_perf_string = ''

        # set optim and scheduler
        nr_train_instances = len(tr_labs)
        nr_train_batches = int(nr_train_instances / b_size)
        half_tr_bs = int(nr_train_instances/2)
        self.optimizer = AdamW(self.model.parameters(), lr=lr, eps=1e-8)

        # self.scheduler = lr_scheduler.CyclicLR(self.optimizer, base_lr=lr, step_size_up=half_tr_bs,
        #                                       cycle_momentum=False, max_lr=lr * 30)

        num_train_optimization_steps = nr_train_batches * n_eps
        num_train_warmup_steps = int(0.1 * num_train_optimization_steps) #warmup_proportion

        # self.scheduler = get_linear_schedule_with_warmup(self.optimizer, num_warmup_steps=num_train_warmup_steps,
        #                                                 num_training_steps=num_train_optimization_steps)  # PyTorch scheduler

    def load_model(self, name):
        cpfp = os.path.join(self.cp_dir, name)
        cp = torch.load(cpfp, map_location=torch.device('cpu'))
        model = cp['model']
        model.load_state_dict(cp['state_dict'])
        self.model = model
        self.model.to(self.device)
        if self.use_cuda: self.model.cuda()
        return model

    def train_on_batch(self, batch):
        batch = tuple(t.to(self.device) for t in batch)
        inputs, labels = batch[:-1], batch[-1]

        self.model.zero_grad()
        logits, probs, _ = self.model(inputs)
        loss = self.criterion(logits.view(-1, 2), labels.view(-1))
        # loss = self.criterion(logits.squeeze(), labels)
        loss.backward()

        torch.nn.utils.clip_grad_norm_(self.model.parameters(), 1.0)
        self.optimizer.step()
        #self.scheduler.step()
        return loss.item()

    def save_model(self, name):
        checkpoint = {'model': self.model,
                      'state_dict': self.model.state_dict(),
                      'optimizer': self.optimizer.state_dict()}
        cpfp = os.path.join(self.cp_dir, name)
        torch.save(checkpoint, cpfp)

    def predict(self, batches):
        self.model.eval()

        y_pred = []
        losses = []
        sum_loss = 0
        embeddings = []
        for step, batch in enumerate(batches):
            batch = tuple(t.to(self.device) for t in batch)
            inputs, labels = batch[:-1], batch[-1]

            with torch.no_grad():
                logits, probs, sentence_representation = self.model(inputs)
                loss = self.criterion(logits.view(-1, 2), labels.view(-1))
                # loss = self.criterion(logits.squeeze(), labels)

                embedding = list(sentence_representation.detach().cpu().numpy())
                embeddings.append(embedding)
                #sigm_output  = self.model(ids, documents, positions)
                #sigm_output = sigm_output.detach().cpu().numpy()
                #loss = self.criterion(sigm_output, labels)

            loss = loss.detach().cpu().numpy() #probs.shape: batchsize * num_classes
            probs = probs.detach().cpu().numpy() #probs.shape: batchsize * num_classes

            losses.append(loss)

            if len(y_pred) == 0:
                y_pred = probs
            else:
                y_pred = np.append(y_pred, probs, axis=0)


                # convert to predictions
                # #preds = [1 if output > 0.5 else 0 for output in sigm_output]
                #y_pred.extend(preds)

            sum_loss += loss.item()

        y_pred = y_pred.squeeze()
        y_pred = np.argmax(y_pred, axis=1)
        # y_pred = [0 if el < 0.5 else 1 for el in y_pred]
        self.model.train()
        return y_pred, sum_loss / len(batches), embeddings, losses

# _, USE_CUDA = get_torch_device()
# LongTensor = torch.cuda.LongTensor if USE_CUDA else torch.LongTensor
# FloatTensor = torch.cuda.FLoatTensor if USE_CUDA else torch.FloatTensor
