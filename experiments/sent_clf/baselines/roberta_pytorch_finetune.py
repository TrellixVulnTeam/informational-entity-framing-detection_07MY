from __future__ import absolute_import, division, print_function
from transformers.optimization import AdamW, get_linear_schedule_with_warmup
from lib.classifiers.RobertaWrapper import RobertaForSequenceClassification, Inferencer, save_model, load_features
from datetime import datetime
import torch
import random, argparse
import numpy as np
from lib.handle_data.PreprocessForBert import *
from lib.utils import get_torch_device, InputFeatures
import logging

'''
class InputFeatures(object):
    """A single set of features of data."""

    def __init__(self, my_id, input_ids, input_mask, segment_ids, label_id):
        self.my_id = my_id
        self.input_ids = input_ids
        self.input_mask = input_mask
        self.segment_ids = segment_ids
        self.label_id = label_id
'''

########################
# WHAT IS THE EXPERIMENT
########################

# find GPU if present
model_mapping = {'rob_base': 'roberta-base',
                 'rob_dapt': 'experiments/adapt_dapt_tapt/pretrained_models/news_roberta_base',
                 'rob_tapt': 'experiments/adapt_dapt_tapt/pretrained_models/dsp_roberta_base_tapt_hyperpartisan_news_5015',
                 'rob_dapttapt': 'experiments/adapt_dapt_tapt/pretrained_models/dsp_roberta_base_dapt_news_tapt_hyperpartisan_news_5015',
                 'rob_basil_tapt': 'experiments/adapt_dapt_tapt/dont-stop-pretraining/roberta-tapt',
                 'rob_basil_dapttapt': 'experiments/adapt_dapt_tapt/dont-stop-pretraining/roberta-dapttapt'
                }
device, USE_CUDA = get_torch_device()

parser = argparse.ArgumentParser()
parser.add_argument('-load', '--load', action='store_true', default=True)
parser.add_argument('-ep', '--n_epochs', type=int, default=10) #2,3,4
parser.add_argument('-debug', '--debug', action='store_true', default=False)

parser.add_argument('-sampler', '--sampler', type=str, default='sequential')
parser.add_argument('-model', '--model', type=str, default=None) #2,3,4
parser.add_argument('-lr', '--lr', type=float, default=None) #5e-5, 3e-5, 2e-5
parser.add_argument('-bs', '--bs', type=int, default=None) #16, 21
parser.add_argument('-sv', '--sv', type=int, default=None) #16, 21
parser.add_argument('-fold', '--fold', type=str, default=None) #16, 21
parser.add_argument('-force_emb', '--force_embed', action='store_true', default=False) #16, 21
args = parser.parse_args()

FORCE_EMBED = args.force_embed
N_EPS = args.n_epochs
models = [args.model] if args.model else ['rob_base']
seeds = [args.sv] if args.sv else [22]
bss = [args.bs] if args.bs else [16]
lrs = [args.lr] if args.lr else [1e-5]
folds = [args.fold] if args.fold else [str(el) for el in range(1,11)]
samplers = [args.sampler] if args.sampler else ['sequential']

DEBUG = args.debug
if DEBUG:
    N_EPS = 2
    seeds = [0]
    bss = [32]
    lrs = [3e-5]
    folds = ['1']
    samplers = ['sequential']

########################
# WHERE ARE THE FILES
########################

TASK_NAME = f'SC_rob'
FEAT_DIR = f'data/sent_clf/features_for_roberta'
CHECKPOINT_DIR = f'models/checkpoints/{TASK_NAME}/'
REPORTS_DIR = f'reports/{TASK_NAME}'
TABLE_DIR = os.path.join(REPORTS_DIR, 'tables')
CACHE_DIR = 'models/cache/'  # This is where BERT will look for pre-trained models to load parameters from.
MAIN_TABLE_FP = os.path.join(TABLE_DIR, f'roberta_ft_results.csv')

#if not os.path.exists(CHECKPOINT_DIR):
#    os.makedirs(CHECKPOINT_DIR)
#if not os.path.exists(CURRENT_BEST_DIR):
#    os.makedirs(CURRENT_BEST_DIR)

if not os.path.exists(REPORTS_DIR):
    os.makedirs(REPORTS_DIR)
if not os.path.exists(TABLE_DIR):
    os.makedirs(TABLE_DIR)
if os.path.exists(MAIN_TABLE_FP):
    table_columns = 'model,sampler,seed,bs,lr,model_loc,fold,epoch,set_type,loss,fn,fp,tn,tp,acc,prec,rec,f1'
    main_results_table = pd.read_csv(MAIN_TABLE_FP)
else:
    table_columns = 'model,sampler,seed,bs,lr,model_loc,fold,epoch,set_type,loss,fn,fp,tn,tp,acc,prec,rec,f1'
    main_results_table = pd.DataFrame(columns=table_columns.split(','))

########################
# MAIN
########################

GRADIENT_ACCUMULATION_STEPS = 1
WARMUP_PROPORTION = 0.1
NUM_LABELS = 2
PRINT_EVERY = 100

inferencer = Inferencer(REPORTS_DIR, logger, device, use_cuda=USE_CUDA)

if __name__ == '__main__':
    # set logger
    now = datetime.now()
    now_string = now.strftime(format=f'%b-%d-%Hh-%-M_{TASK_NAME}')
    LOG_NAME = f"{REPORTS_DIR}/{now_string}.log"
    console_hdlr = logging.StreamHandler(sys.stdout)
    file_hdlr = logging.FileHandler(filename=LOG_NAME)
    logging.basicConfig(level=logging.INFO, handlers=[console_hdlr, file_hdlr])
    logger = logging.getLogger()
    logger.info(args)

    for MODEL in models:
        ROBERTA_MODEL = model_mapping[MODEL]
        for SAMPLER in samplers:
            for SEED in seeds:
                if SEED == 0:
                    SEED_VAL = random.randint(0, 300)
                else:
                    SEED_VAL = SEED

                seed_name = f"{MODEL}_{SAMPLER}_{SEED_VAL}"
                random.seed(SEED_VAL)
                np.random.seed(SEED_VAL)
                torch.manual_seed(SEED_VAL)
                torch.cuda.manual_seed_all(SEED_VAL)

                for BATCH_SIZE in bss:
                    bs_name = seed_name + f"_bs{BATCH_SIZE}"
                    for LEARNING_RATE in lrs:
                        setting_name = bs_name + f"_lr{LEARNING_RATE}"
                        setting_results_table = pd.DataFrame(columns=table_columns.split(','))
                        for fold_name in folds:
                            fold_results_table = pd.DataFrame(columns=table_columns.split(','))
                            name = setting_name + f"_f{fold_name}"

                            # init results containers
                            best_model_loc = os.path.join(CHECKPOINT_DIR, name)
                            best_val_res = {'model': MODEL, 'seed': SEED_VAL, 'fold': fold_name, 'bs': BATCH_SIZE, 'lr': LEARNING_RATE, 'set_type': 'dev',
                                            'f1': 0, 'model_loc': best_model_loc, 'sampler': SAMPLER}
                            test_res = {'model': MODEL, 'seed': SEED_VAL, 'fold': fold_name, 'bs': BATCH_SIZE, 'lr': LEARNING_RATE, 'set_type': 'test',
                                        'sampler': SAMPLER}

                            # load feats
                            train_fp = os.path.join(FEAT_DIR, f"{fold_name}_train_features.pkl")
                            dev_fp = os.path.join(FEAT_DIR, f"{fold_name}_dev_features.pkl")
                            test_fp = os.path.join(FEAT_DIR, f"{fold_name}_test_features.pkl")
                            _, train_batches, train_labels = load_features(train_fp, BATCH_SIZE, SAMPLER)
                            _, dev_batches, dev_labels = load_features(dev_fp, 1, SAMPLER)
                            test_ids, test_batches, test_labels = load_features(test_fp, 1, SAMPLER)

                            # start training
                            logger.info(f"***** Training on Fold {fold_name} *****")
                            logger.info(f"  Details: {best_val_res}")
                            logger.info(f"  Logging to {LOG_NAME}")

                            FORCE = False
                            if not os.path.exists(best_model_loc) or FORCE:
                                model = RobertaForSequenceClassification.from_pretrained(ROBERTA_MODEL,
                                                                                         cache_dir=CACHE_DIR,
                                                                                         num_labels=NUM_LABELS,
                                                                                         output_hidden_states=True,
                                                                                         output_attentions=False)
                                model.to(device)
                                optimizer = AdamW(model.parameters(), lr=LEARNING_RATE, weight_decay=0.01,
                                                  eps=1e-6)  # To reproduce BertAdam specific behavior set correct_bias=False

                                n_train_batches = len(train_batches)
                                half_train_batches = int(n_train_batches / 2)
                                GRADIENT_ACCUMULATION_STEPS = 2
                                WARMUP_PROPORTION = 0.06
                                num_tr_opt_steps = n_train_batches * N_EPS / GRADIENT_ACCUMULATION_STEPS
                                num_tr_warmup_steps = int(WARMUP_PROPORTION * num_tr_opt_steps)
                                scheduler = get_linear_schedule_with_warmup(optimizer,
                                                                            num_warmup_steps=num_tr_warmup_steps,
                                                                            num_training_steps=num_tr_opt_steps)

                                model.train()

                                for ep in range(1, N_EPS + 1):
                                    epoch_name = name + f"_ep{ep}"
                                    tr_loss = 0
                                    for step, batch in enumerate(train_batches):
                                        batch = tuple(t.to(device) for t in batch)

                                        model.zero_grad()
                                        outputs = model(batch[0], batch[1], labels=batch[2])
                                        (loss), logits, pooled_output, sequence_output, hidden_states = outputs

                                        loss.backward()
                                        tr_loss += loss.item()
                                        optimizer.step()
                                        scheduler.step()

                                        if step % PRINT_EVERY == 0 and step != 0:
                                            logging.info(f' Ep {ep} / {N_EPS} - {step} / {len(train_batches)} - Loss: {loss.item()}')

                                    av_loss = tr_loss / len(train_batches)
                                    # save_model(model, CHECKPOINT_DIR, epoch_name)

                                    dev_mets, dev_perf = inferencer.evaluate(model, dev_batches, dev_labels, av_loss=av_loss,
                                                                                 set_type='dev', name=epoch_name)

                                    test_mets, test_perf = inferencer.evaluate(model, test_batches, test_labels,
                                                                             av_loss=av_loss,
                                                                             set_type='test', name=epoch_name)

                                    # check if best
                                    high_score = ''
                                    if dev_mets['f1'] > best_val_res['f1']:
                                        best_val_res.update(dev_mets)
                                        high_score = '(HIGH SCORE)'
                                        save_model(model, CHECKPOINT_DIR, name)

                                    logger.info(f'{epoch_name}: {dev_perf} {high_score}')
                                    logger.info(f'{epoch_name}: {test_perf}')

                            best_model = RobertaForSequenceClassification.from_pretrained(best_model_loc,
                                                                                          num_labels=NUM_LABELS,
                                                                                          output_hidden_states=True,
                                                                                          output_attentions=False)
                            best_model.to(device)

                            logger.info(f"***** Best model on Fold {fold_name} *****")
                            logger.info(f"  Details: {best_val_res}")
                            dev_mets, dev_perf = inferencer.evaluate(best_model, dev_batches, dev_labels, set_type='dev')
                            best_val_res.update(dev_mets)
                            logging.info(f"{dev_perf}")

                            preds, labels = inferencer.predict(best_model, test_batches)
                            assert len(preds) == len(test_ids)

                            basil_w_pred = pd.DataFrame(index=test_ids)
                            basil_w_pred['pred'] = preds
                            basil_w_pred['label'] = test_labels
                            pred_fp = f'data/test_w_preds/test_w_rob_{SEED_VAL}_preds/{fold_name}_test_w_pred.csv'
                            basil_w_pred.to_csv(pred_fp)
                            logger.info(f'Preds in {pred_fp}')

                            test_mets, test_perf = inferencer.evaluate(best_model, test_batches, test_labels, set_type='test')
                            test_res.update(test_mets)
                            logging.info(f"{test_perf}")

                            '''
                            for EMB_TYPE in ['cross4bert']: #poolbert', 'avbert', 'unpoolbert', 'crossbert'
                                emb_fp = f'data/embeddings/{MODEL}/{name}_basil_w_{EMB_TYPE}'

                                if not os.path.exists(emb_fp):
                                    logging.info(f'Generating {EMB_TYPE} ({emb_fp})')
                                    feat_fp = os.path.join(FEAT_DIR, f"all_features.pkl")
                                    all_ids, all_batches, all_labels = load_features(feat_fp, batch_size=1, sampler=SAMPLER)
                                    embs = inferencer.predict(best_model, all_batches, return_embeddings=True, emb_type=EMB_TYPE)

                                    assert len(embs) == len(all_ids)

                                    basil_w_BERT = pd.DataFrame(index=all_ids)
                                    basil_w_BERT[EMB_TYPE] = embs
                                    basil_w_BERT.to_csv(emb_fp)
                                    logger.info(f'{EMB_TYPE} embeddings in {emb_fp}.csv')
                            '''

                            # store performance in table
                            fold_results_table = fold_results_table.append(best_val_res, ignore_index=True)
                            fold_results_table = fold_results_table.append(test_res, ignore_index=True)
                            setting_results_table = setting_results_table.append(fold_results_table)

                            # print result on fold
                            logging.info(f'Fold {fold_name} results: \n{fold_results_table[["model", "seed", "bs", "lr", "fold", "set_type", "f1"]]}')

                        # print result of setting
                        logging.info(
                            f'Setting {setting_name} results: \n{setting_results_table[["model", "seed", "bs", "lr", "fold", "set_type", "f1"]]}')

                        # store performance of setting
                        main_results_table = main_results_table.append(setting_results_table, ignore_index=True)

                        # write performance to file
                        setting_fp = os.path.join(TABLE_DIR, f'{setting_name}_results_table.csv')
                        orig_setting_results_table = pd.read_csv(setting_fp)
                        setting_results_table = pd.concat((orig_setting_results_table, setting_results_table))
                        #setting_results_table = setting_results_table.drop_duplicates(keep='first')
                        setting_results_table.to_csv(setting_fp, index=False)
                        logging.info(
                            f'To {setting_fp}')

                        main_results_table.to_csv(MAIN_TABLE_FP, index=False)

'''
n_train_batches = len(train_batches)
half_train_batches = int(n_train_batches / 2)
num_tr_opt_steps = n_train_batches * NUM_TRAIN_EPOCHS  # / GRADIENT_ACCUMULATION_STEPS
num_tr_warmup_steps = int(WARMUP_PROPORTION * num_tr_opt_steps)
#scheduler = get_linear_schedule_with_warmup(optimizer, num_warmup_steps=num_tr_warmup_steps, num_training_steps=num_tr_opt_steps)
'''