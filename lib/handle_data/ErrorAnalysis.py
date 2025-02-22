import pandas as pd
from lib.utils import standardise_id
from lib.evaluate.Eval import my_eval
import re
from collections import Counter
import numpy as np


def extract_e(string):
    string = re.sub("[\'\[\]]", "", string)
    l = string.split(', ')
    return l


def top_in_row(x, top_e):
    if isinstance(top_e, str):
        top_e = [top_e]
    top_e = set(top_e)
    row_e = set(extract_e(x))
    diff = top_e.intersection(row_e)
    return bool(diff)


def flatten_e(top_e):
    ents = []
    for e in top_e:
        ents.extend(extract_e(e))
    return ents


def collect_preds(model, context):
    df = pd.DataFrame()
    for f in [str(el) for el in range(1, 11)]:
        subdf = pd.read_csv(f"data/test_w_preds/test_w_{model}_{context}_preds/{f}_test_w_pred.csv", index_col=0)
        df = df.append(subdf)

    df.index = [standardise_id(el) for el in df.index]
    return df


def lat(x):
    out = []
    for el in x:
        el = str(el)
        if '.' in el and len(el) == 4:
            el += '0'
        #el = '$' + el + '$'
        out.append(el)
    return out


def got_quote(x):
    double_q = '"' in str(x)
    return double_q


def bin_length(len, quantiles):
    if len <= quantiles[0]:
        return "0-90"
    elif len <= quantiles[1]:
        return "91-137"
    elif len <= quantiles[2]:
        return "138-192"
    elif len <= quantiles[3]:
        return "193-647"


def give_subj_score(x, sent_lex, subj_words):
    tokens = x.split(' ')
    subj_score = 0
    for t in tokens:
        if t in subj_words:
            subj_score += sent_lex.loc[t, 'subj_score']
    norm = subj_score / len(tokens)
    return round(norm * 100, 2)


def bin_subj_score(subj_score, quantiles):
    if subj_score == 0:
        return "No"
    elif subj_score > 0 and subj_score <= quantiles[0]:
        return "No" #"0-3.7" #"1-5.26"
    else: # subj_score <= quantiles[1]:
        return "Yes" #"5.37-8.57"
    #elif subj_score <= quantiles[2]:
    #    return "9.53-66.67" #"8.58-13.51"
    #elif subj_score <= quantiles[3]:
    #    return "13.52-66.67"


def load_mpqa_lex():
    "input looks like: type=weaksubj len=1 word1=abandoned pos1=adj stemmed1=n priorpolarity=negative"
    fn = 'subjectivity_clues_hltemnlp05/subjclueslen1-HLTEMNLP05.tff'
    with open(fn, 'r') as f:
        ls = f.readlines()
        tags = [tag.split('=')[0] for tag in ls[0].split(' ')]
        vals = [[tag.split('=')[1].strip() for tag in l.split(' ')] for l in ls]
    lex = pd.DataFrame(vals, columns=tags)
    lex = lex[((lex.pos1 == 'verb') | (lex.pos1 == 'anypos')) & (lex.priorpolarity != 'both')]
    lex.priorpolarity = lex.priorpolarity.replace({'neutral': 0, 'positive': 1, 'negative': -1})
    lex.priorpolarity = lex.priorpolarity.replace({'neutral_eb': 0, 'positive_eb': 1, 'negative_eb': -1}).astype(float)
    lex.priorpolarity = lex.priorpolarity.astype(float)
    lex['subj_score'] = lex.type.apply(lambda x: 1 if x == 'weaksubj' else 2)
    #weakmask = lex.type.str.startswith('weak')
    #lex.loc[weakmask, 'priorpolarity'] = lex[weakmask]['priorpolarity'] / 2
    #lex = lex[['word1', 'subj_score']]
    lex = lex.drop_duplicates('word1')
    lex = lex.set_index('word1')
    return lex


models2compare = {'all':
                  [('cam+', 'article'), ('cam+', 'story'), ('rob', 'none')], # ('cam++', 'article'), ('cam++', 'story'),
                  'base_best':
                  [('rob', '22'), ('cim', 'coverage')], # ('cim', 'article'),
                  'cimcov':
                   [('cim', 'coverage')] #[('cam+', 'story')]
                  }


class ErrorAnalysis:
    """
    Functions to analysis basil w preds
    """

    def __init__(self, models):
        self.models = models2compare[models]

        self.sent_lex = load_mpqa_lex()
        self.subj_words = set(self.sent_lex.index.values)

        self.w_preds = self.contruct_df()
        self.N = len(self.w_preds)

    def contruct_df(self):
        out = pd.read_csv('data/basil.csv', index_col=0).fillna('')
        out.index = [standardise_id(el) for el in out.index]

        for model, context in self.models:
            predn = f'{model}_{context}'
            df = collect_preds(model, context)
            out.loc[df.index, predn] = df.pred

        out = out.fillna(0)
        out.main_entities = out.main_entities.apply(lambda x: re.sub('Lawmakers', 'lawmakers', x))
        out['source'] = [el.lower() for el in out.source]
        out['article'] = out.source + out.sent_idx.astype(str)
        out['quote'] = out.sentence.apply(got_quote)
        out['len'] = out.sentence.apply(len)
        len_quantiles = out.len.quantile([0.25, 0.5, 0.75, 1.0]).values
        out['len'] = out.len.apply(lambda x: bin_length(x, len_quantiles))
        out['subj'] = out.sentence.apply(lambda x: give_subj_score(x, self.sent_lex, self.subj_words))
        subj_pos_quantiles = out.subj.quantile([0.5, 0.75, 1.0]).values
        out['subj'] = out.subj.apply(lambda x: bin_subj_score(x, subj_pos_quantiles))
        return out

    def inf_bias_only(self):
        self.w_preds = self.w_preds[self.w_preds.bias == 1]

    def no_bias_only(self):
        self.w_preds = self.w_preds[(self.w_preds.bias == 0) & (self.w_preds.lex_bias == 0)]

    def row4compare(self, n, gr=None, model=None, context=None):
        N = len(gr)
        Nbias = sum(gr.bias == 1)
        Percbias = str(round(Nbias / N * 100,2)) + '%'
        mets, _ = my_eval(gr.bias, gr[f'{model}_{context}'])
        mets = [round(el*100, 2) for el in [mets['prec'], mets['rec'], mets['f1']]]

        return [n] + lat([N, Percbias] + mets)

    def compare_subsets(self, df, grby, model, context):
        basic_columns = [grby, 'N', '%Bias', 'Prec', 'Rec', 'F1']

        out = pd.DataFrame(columns=basic_columns)

        if grby is not None:
            for n, gr in df.groupby(grby):
                r = self.row4compare(n, gr, model, context)

                rows = pd.DataFrame([r], columns=basic_columns)
                out = out.append(rows, ignore_index=True)

        r = self.row4compare(f'{model}_{context}', df, model, context)

        row = pd.DataFrame([r], columns=basic_columns)
        out = out.append(row, ignore_index=True)
        return out

    def conf_mat(self, df, model, context):
        predn = f'{model}_{context}'

        df['gp'] = (df.bias == 1)
        df['pp'] = (df[predn] == 1)

        df['gn'] = (df.bias == 0)
        df['pn'] = (df[predn] == 0)

        df['tp'] = (df.bias == 1) & (df[predn] == 1)
        df['fp'] = (df.bias == 0) & (df[predn] == 1)
        df['tn'] = (df.bias == 0) & (df[predn] == 0)
        df['fn'] = (df.bias == 1) &(df[predn] == 0)
        return df

    def concat_comparisons(self, dfs, only_rec=False, incl_lex=False):
        info_col_n = 3
        basic_info = dfs[1].iloc[:,:info_col_n]
        new_df = pd.DataFrame(basic_info, columns=dfs[1].columns[:info_col_n])

        for df in dfs:
            model = df.iloc[-1,0]
            df = df.iloc[:,info_col_n:]
            df.columns = [el + '_' + model for el in df.columns]
            if only_rec:
                df = df.iloc[:,1:-1]
            new_df[df.columns] = df

        new_df.iloc[-1,0] = 'All'
        new_df = new_df.set_index(new_df.columns[0])
        return new_df

    def clean_for_pol_analysis(self):
        pol_df = self.w_preds.copy()
        dirt_pol = pol_df[pol_df.inf_pol.isin(["['Positive', 'Negative']", "['Negative', 'Positive']"])]
        pol_df = pol_df.drop(dirt_pol.index)
        pol_df = pol_df.replace({"['Negative', 'Negative']": "['Negative']"})
        pol_df = pol_df.replace({"['Negative']": "Negative", "['Positive']": "Positive", })
        return pol_df

    def clean_for_dir_analysis(self):
        dir_df = self.w_preds.copy()
        dirt_dir = dir_df[dir_df.inf_dir.isin(["['Direct', 'Indirect (Ally)']]", "['Indirect (General)', 'Direct']",
                                               "['Indirect (Ally)', 'Direct']", "['Direct', 'Indirect (Ally)']"])]
        dir_df = dir_df.drop(dirt_dir.index)
        dir_df = dir_df.replace({"['Direct', 'Direct']": "['Direct']",
                                 "['Indirect (Ally)']": "['Indirect']",
                                 "['Indirect (Opponent)']": "['Indirect']",
                                 "['Indirect (General)']": "['Indirect']"})
        dir_df = dir_df.replace({"['Direct']": "Direct", "['Indirect']": "Indirect"})
        return dir_df

    def get_top_e(self, e='main', n=10):
        if e == 'main':
            es = self.w_preds.main_entities.values
        elif e == 'target':
            es = self.w_preds.inf_entities.values
        ents = flatten_e(es)
        top_e = Counter(ents).most_common(n)
        return top_e

    def add_top_e(self, df, top_e):
        tope_in_main = []
        for i, r in df.iterrows():
            te_in_me = False
            for e, c in top_e:
                te_in_me = e in r.main_entities
            tope_in_main.append(te_in_me)
        df['tope_in_me'] = tope_in_main
        return df

    def add_e(self):
        df = self.w_preds

        tar_in_art = []
        tar_in_sent = []
        for i, r in df.iterrows():
            t_in_a, t_in_s = self.where_tar(r)
            tar_in_art.append(t_in_a)
            tar_in_sent.append(t_in_s)

        df['tar_in_art'] = tar_in_art
        df['tar_in_sent'] = tar_in_sent

        df['tar_art_only'] = False
        df.loc[df.tar_in_art & -df.tar_in_sent, 'tar_art_only'] = True
        return df

    def analyze_top_e(self, df, top_e, model, context):
        basic_columns = ['entity', 'N', '#Bias', 'Prec', 'Rec', 'F1']

        out = pd.DataFrame(columns=basic_columns)
        for e, c in top_e:
            N = c
            #Nbias = sum(df.inf_entities.apply(lambda x: e in x))

            gr = df[(df.main_entities.apply(lambda x: e in x))]

            r = self.row4compare(e, gr, model, context)
            row = pd.DataFrame([r], columns=basic_columns)
            out = out.append(row, ignore_index=True)

        r = self.row4compare(f'{model}_{context}', df, model, context)
        row = pd.DataFrame([r], columns=basic_columns)
        out = out.append(row, ignore_index=True)

        return out

    def get_surface_e(self, e):
        surface_mapping = {'Republican lawmakers': 'Republican', 'Democratic lawmakers': 'Democrat', 'Republicans': 'Republican',
                           'House Democrats': 'Democrat'}

        if e not in ['Republican lawmakers', 'Democratic lawmakers', 'House Democrats', 'Republicans']:
            surface = e.split(' ')[-1]
        else:
            surface = surface_mapping[e]
        return surface

    def get_e_abrev(self, e):
        ab, rev = e.split(' ') if len(e.split(' ')) == 2 else [e, '']
        abrev = ab[0] + rev[:3] if rev else ab[:3]
        return abrev

    def where_tar(self, row):
        tar_in_a = False
        tar_in_sent = False

        a = extract_e(row.main_entities)
        ts = extract_e(row.inf_entities)

        for t in ts:
            if t in set(a):
                tar_in_a = True

            if isinstance(row.sentence, str):
                s = self.get_surface_e(t)
                if s in row.sentence:
                    tar_in_sent = True

        return tar_in_a, tar_in_sent

    def sample_sentences(self, df, which='pol'):
        sample = []
        for n, gr in df.groupby(which):
            #print(n, len(gr))

            samp = gr.sample(5)[['sentence', 'inf_entities']]
            #print(samp)
        return sample

    def negative_inf_lex_bias(self):
        df = self.w_preds
        for n, art in df.groupby(['article']):
            inf_bias = art[['inf_pol', 'lex_pol']]  # [art['bias'] == 1]
            print(inf_bias)

'''
for e, c in top_e:
    s = surface_e[e]
    abrev = self.get_e_abrev(e)
    df[f'{abrev}_sent'] = df.sentence.apply(lambda x: (s in x) if isinstance(x, str) else False)
    df[f'{abrev}_art'] = df.main_entities.apply(lambda x: top_in_row(x, e))
'''