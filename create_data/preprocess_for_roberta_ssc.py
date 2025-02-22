from __future__ import absolute_import, division, print_function
from transformers import RobertaTokenizer
from transformers.configuration_roberta import RobertaConfig
import pickle
from lib.handle_data.PreprocessForRoberta import *
import csv
from lib.handle_data.SplitData import split_input_for_bert
import torch
import argparse


def preprocess(rows):
    count = 0
    total = len(rows)
    features = []
    for row in rows:
        feats = convert_example_to_feature(row)
        features.append(feats)
        count += 1

        if count % 500 == 0:
            status = f'Processed {count}/{total} rows'
            print(status)
    return features


def enforce_max_sent_per_example(sentences, max_sent_per_example, labels=None):
    """
    Splits examples with len(sentences) > self.max_sent_per_example into multiple smaller examples
    with len(sentences) <= self.max_sent_per_example.
    Recursively split the list of sentences into two halves until each half
    has len(sentences) < <= self.max_sent_per_example. The goal is to produce splits that are of almost
    equal size to avoid the scenario where all splits are of size
    self.max_sent_per_example then the last split is 1 or 2 sentences
    This will result into losing context around the edges of each examples.
    """
    if labels is not None:
        assert len(sentences) == len(labels)

    if len(sentences) > max_sent_per_example > 0:
        i = len(sentences) // 2
        l1 = enforce_max_sent_per_example(
                sentences[:i], max_sent_per_example, None if labels is None else labels[i:])
        l2 = enforce_max_sent_per_example(
                sentences[i:], max_sent_per_example, None if labels is None else labels[i:])
        return l1 + l2
    else:
        return [sentences]


def flatten_sequence(seq_rows, cls, pad, max_ex_len, max_sent):
    flat_input_ids = []
    flat_labels = []
    #segment_ids = []

    for i, sent in enumerate(seq_rows):
        input_ids = remove_special(sent.input_ids, cls, pad)
        flat_input_ids.extend(input_ids)
        flat_labels.append(sent.label_id)
        #segment_ids.extend([i+1] * len(input_ids))

    pad_len = max_ex_len - len(flat_input_ids)
    mask = [1] * len(flat_input_ids) + [0] * pad_len
    flat_input_ids += [pad] * pad_len
    #segment_ids += [pad] * pad_len

    assert len(mask) == len(flat_input_ids)

    lab_pad_len = max_sent - len(flat_labels)
    flat_labels += [-1] * lab_pad_len

    return InputFeatures(my_id=None,
                         input_ids=flat_input_ids,
                         input_mask=mask,
                         segment_ids=[],
                         label_id=flat_labels)


def remove_special(x, cls=0, pad=1):
    return [el for el in x if el not in [cls, pad]]


def seps(x):
    #mask = x == 2
    return [el for el in x if el == 2]#x[mask]


def redistribute_feats(features, cls=0, pad=1, max_sent=10, max_len=None):
    ''' Takes rows of features (each row is sentence), and converts them to rows of multiple sentences '''

    article_rows = {}
    for f in features:
        row = article_rows.setdefault(f.article, [])
        row.append(f)

    sequence_rows = []
    for row in article_rows.values():
        row = sorted(row, key=lambda x: x.sent_id, reverse=False)
        sequences = enforce_max_sent_per_example(row, max_sent)
        for s in sequences:
            sequence_rows.append(s)

    # help measure what the maxlen should be
    for row in sequence_rows:
        toks = [remove_special(f.input_ids, cls, pad) for f in row]
        exlen = sum([len(t) for t in toks])
        if exlen > max_len:
            max_len = exlen
            print('MAX EX LEN:', max_len)

    finfeats = []
    for row in sequence_rows:
        ff = flatten_sequence(row, cls, pad, max_len, max_sent)
        finfeats.append(ff)
    return finfeats


parser = argparse.ArgumentParser()
parser.add_argument('-seqlen', '--sequence_length', type=int, default=1, help='Number of sentences per example#') #2,3,4
args = parser.parse_args()

# choose sentence or bio labels
task = 'sent_clf'
DATA_DIR = f'data/{task}/ft_input'

if not os.path.exists(DATA_DIR):
    os.makedirs(DATA_DIR)

# load and split data
folds = split_input_for_bert(DATA_DIR, False, 1, 99)
MAX_DOC_LEN = 76
MAX_SENT_LEN = 486
MAX_EX_LEN = args.sequence_length

# structure of project
CONTEXT_TYPE = 'article'
FEAT_DIR = f'data/sent_clf/features_for_roberta_ssc/ssc{MAX_EX_LEN}/'
DEBUG = False
SUBSET = 1.0 if not DEBUG else 0.1

# The maximum total input sequence length after WordPiece tokenization.
# Sequences longer than this will be truncated, and sequences shorter than this will be padded.
MAX_SEQ_LENGTH = 124
max_lens = {3: 204, 4: 230, 5: 305, 6: 316, 7: 355, 8: 416, 9: 499, 10: 499}
MAX_SEQ_LEN_SSC = max_lens[args.sequence_length]
OUTPUT_MODE = 'classification' # or 'classification', or 'regression'
NR_FOLDS = len(folds)

if OUTPUT_MODE == 'bio_classification':
    spacy_tokenizer = spacy.load("en_core_web_sm")
    tokenizer = RobertaTokenizer.from_pretrained('roberta-base', do_lower_case=False, do_basic_tokenize=False)
else:
    spacy_tokenizer = None
    tokenizer = RobertaTokenizer.from_pretrained('roberta-base')

dataloader = BinaryClassificationProcessor()

label_list = dataloader.get_labels(output_mode=OUTPUT_MODE)  # [0, 1] for binary classification
label_map = {label: i for i, label in enumerate(label_list)}

config = RobertaConfig.from_pretrained('roberta-base')
config.num_labels = len(label_map)

all_infp = os.path.join(DATA_DIR, f"all.tsv")
ofp = os.path.join(FEAT_DIR, f"all_features.pkl")

FORCE = False
if not os.path.exists(ofp) or FORCE:
    examples = dataloader.get_examples(all_infp, 'train', sep='\t')

    examples = [(example, label_map, MAX_SEQ_LENGTH, tokenizer, spacy_tokenizer, OUTPUT_MODE) for example in examples if example.text_a]
    features = preprocess(examples)
    features_dict = {feat.my_id: feat for feat in features}
    print(f"Processed fold all - {len(features)} items")

    with open(ofp, "wb") as f:
        pickle.dump(features, f)
else:
    with open(ofp, "rb") as f:
       features = pickle.load(f)
       features_dict = {feat.my_id: feat for feat in features}
       print(f"Processed fold all - {len(features)} items")

# start
av = 0
for fold in folds:
    fold_name = fold['name']
    for set_type in ['train', 'dev', 'test']:
        infp = os.path.join(DATA_DIR, f"{fold_name}_{set_type}.tsv")
        ofp = os.path.join(FEAT_DIR, f"{fold_name}_{set_type}_features.pkl")

        examples = dataloader.get_examples(infp, set_type, sep='\t')

        features = [features_dict[example.my_id] for example in examples if example.text_a]

        features = redistribute_feats(features, cls=0, pad=1, max_sent=MAX_EX_LEN, max_len=MAX_SEQ_LEN_SSC)

        #for f in features[:10]:
        #    print(len(f.input_ids), f.input_ids, len(f.label_id), f.label_id)

        # print(features[0].input_ids)
        if set_type == 'train':
            av += len(features)
        print(f"Processed fold {fold_name} {set_type} - {len(features)} items and writing to {ofp}")

        with open(ofp, "wb") as f:
            pickle.dump(features, f)

print(av/10)
tokenizer.save_vocabulary(FEAT_DIR)
