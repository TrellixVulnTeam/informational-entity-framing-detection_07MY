from transformers import BertPreTrainedModel, BertModel
from torch.nn import Dropout, Linear
from torch.nn import CrossEntropyLoss, MSELoss
import torch
from torch import nn
import numpy as np
from torch.utils.data import (DataLoader, SequentialSampler, TensorDataset)
from lib.evaluate.Eval import eval
import os


def to_tensor(features, OUTPUT_MODE):
    example_ids = [f.my_id for f in features]
    input_ids = torch.tensor([f.input_ids for f in features], dtype=torch.long)
    input_mask = torch.tensor([f.input_mask for f in features], dtype=torch.long)
    segment_ids = torch.tensor([f.segment_ids for f in features], dtype=torch.long)

    if OUTPUT_MODE == "classification":
        label_ids = torch.tensor([f.label_id for f in features], dtype=torch.long)
    elif OUTPUT_MODE == "regression":
        label_ids = torch.tensor([f.label_id for f in features], dtype=torch.float)

    data = TensorDataset(input_ids, input_mask, segment_ids, label_ids)
    #return to_tensors() example_ids, data, label_ids  # example_ids, input_ids, input_mask, segment_ids, label_ids
    return to_tensors(features=features)


class InputFeatures(object):
    """A single set of features of data."""

    def __init__(self, my_id, input_ids, input_mask, segment_ids, label_id):
        self.my_id = my_id
        self.input_ids = input_ids
        self.input_mask = input_mask
        self.segment_ids = segment_ids
        self.label_id = label_id

# model

class BertForSequenceClassification(BertPreTrainedModel):
    def __init__(self, config):
        super().__init__(config)
        self.num_labels = config.num_labels

        self.bert = BertModel(config)
        self.dropout = Dropout(config.hidden_dropout_prob)
        self.classifier = Linear(config.hidden_size, self.config.num_labels)
        self.sigm = nn.Sigmoid()

        self.init_weights()

    def forward(
        self,
        input_ids=None,
        attention_mask=None,
        token_type_ids=None,
        position_ids=None,
        head_mask=None,
        inputs_embeds=None,
        labels=None,
    ):
        r"""
        labels (:obj:`torch.LongTensor` of shape :obj:`(batch_size,)`, `optional`, defaults to :obj:`None`):
            Labels for computing the sequence classification/regression loss.
            Indices should be in :obj:`[0, ..., config.num_labels - 1]`.
            If :obj:`config.num_labels == 1` a regression loss is computed (Mean-Square loss),
            If :obj:`config.num_labels > 1` a classification loss is computed (Cross-Entropy).

    Returns:
        :obj:`tuple(torch.FloatTensor)` comprising various elements depending on the configuration (:class:`~transformers.BertConfig`) and inputs:
        loss (:obj:`torch.FloatTensor` of shape :obj:`(1,)`, `optional`, returned when :obj:`label` is provided):
            Classification (or regression if config.num_labels==1) loss.
        logits (:obj:`torch.FloatTensor` of shape :obj:`(batch_size, config.num_labels)`):
            Classification (or regression if config.num_labels==1) scores (before SoftMax).
        hidden_states (:obj:`tuple(torch.FloatTensor)`, `optional`, returned when ``config.output_hidden_states=True``):
            Tuple of :obj:`torch.FloatTensor` (one for the output of the embeddings + one for the output of each layer)
            of shape :obj:`(batch_size, sequence_length, hidden_size)`.

            Hidden-states of the model at the output of each layer plus the initial embedding outputs.
        attentions (:obj:`tuple(torch.FloatTensor)`, `optional`, returned when ``config.output_attentions=True``):
            Tuple of :obj:`torch.FloatTensor` (one for each layer) of shape
            :obj:`(batch_size, num_heads, sequence_length, sequence_length)`.

            Attentions weights after the attention softmax, used to compute the weighted average in the self-attention
            heads.

    Examples::

        from transformers import BertTokenizer, BertForSequenceClassification
        import torch

        tokenizer = BertTokenizer.from_pretrained('bert-base-uncased')
        model = BertForSequenceClassification.from_pretrained('bert-base-uncased')

        input_ids = torch.tensor(tokenizer.encode("Hello, my dog is cute", add_special_tokens=True)).unsqueeze(0)  # Batch size 1
        labels = torch.tensor([1]).unsqueeze(0)  # Batch size 1
        outputs = model(input_ids, labels=labels)

        loss, logits = outputs[:2]

        """

        outputs = self.bert(
            input_ids,
            attention_mask=attention_mask,
            token_type_ids=token_type_ids,
            position_ids=position_ids,
            head_mask=head_mask,
            inputs_embeds=inputs_embeds,
        )

        sequence_output = outputs[0] # according to pytorch doc: (batch_size, sequence_length, hidden_size)
        pooled_output = outputs[1]

        pooled_output = self.dropout(pooled_output) # 1 * 768
        logits = self.classifier(pooled_output)
        probs = self.sigm(logits)

        outputs = (logits, probs,) + (sequence_output, pooled_output) #+ outputs[2:]  # add hidden states and attention if they are here

        if labels is not None:
            if self.num_labels == 1:
                #  We are doing regression
                loss_fct = MSELoss()
                loss = loss_fct(logits.view(-1), labels.view(-1))
            else:
                loss_fct = CrossEntropyLoss()
                loss = loss_fct(logits.view(-1, self.num_labels), labels.view(-1))
            outputs = (loss,) + outputs

        return outputs  # (loss), logits, probs, sequence_ouput,pooled_output), # (hidden_states), (attentions)


class Inferencer():
    def __init__(self, reports_dir, output_mode, logger, device, use_cuda):
        self.device = device
        self.output_mode = output_mode
        self.reports_dir = reports_dir
        self.logger = logger
        self.device = device
        self.use_cuda = use_cuda

    def predict(self, model, data, return_embeddings=False, emb_type='poolbert'):
        model.to(self.device)
        model.eval()

        preds = []
        embeddings = []
        for step, batch in enumerate(data):
            batch = tuple(t.to(self.device) for t in batch)
            input_ids, input_mask, label_ids = batch

            with torch.no_grad():
                outputs = model(input_ids, input_mask, labels=None)
                logits, probs, sequence_output, pooled_output = outputs

            # of last hidden state with size (batch_size, sequence_length, hidden_size)
            # where batch_size=1, sequence_length=95, hidden_size=768)
            # take average of sequence, size (batch_size, hidden_size)
            if emb_type == 'poolbert':
                emb_output = pooled_output
            elif emb_type == "avbert":
                emb_output = sequence_output.mean(axis=1)

            if self.use_cuda:
                emb_output = list(emb_output.detach().cpu().numpy())  # .detach().cpu() necessary here on gpu

            else:
                emb_output = list(emb_output.numpy())
            embeddings.append(emb_output)

            if len(preds) == 0:
                preds.append(probs.detach().cpu().numpy())
            else:
                preds[0] = np.append(preds[0], probs.detach().cpu().numpy(), axis=0)

        preds = preds[0]
        if self.output_mode == "classification":
            preds = np.argmax(preds, axis=1)
        elif self.output_mode == "regression":
            preds = np.squeeze(preds)

        model.train()
        if return_embeddings:
            return embeddings
        else:
            return preds

    def eval(self, model, data, labels, av_loss=None, set_type='dev', name='Basil'):
        preds = self.predict(model, data)
        metrics_dict, metrics_string = eval(labels.numpy(), preds, set_type=set_type, av_loss=av_loss, name=name)

        #output_eval_file = os.path.join(self.reports_dir, f"{name}_eval_results.txt")
        #self.logger.info(f'{metrics_string}')
        #with open(output_eval_file, 'w') as f:
        #    f.write{metrics_string + '\n'}

        return metrics_dict, metrics_string


def save_model(model_to_save, model_dir, identifier):
    output_dir = os.path.join(model_dir, identifier)
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)
    output_model_file = os.path.join(output_dir, "pytorch_model.bin")
    output_config_file = os.path.join(output_dir, "config.json")

    model_to_save = model_to_save.module if hasattr(model_to_save, 'module') else model_to_save  # Only save the model it-self
    torch.save(model_to_save.state_dict(), output_model_file)
    model_to_save.config.to_json_file(output_config_file)

    #test again



