import random
import json
import os, re
import pandas as pd


# Berg split helpers

def order_stories(basil):
    sizes = basil.story.value_counts()
    return sizes.index.to_list()


def cut_in_ten(ordered_stories):
    n_stories = len(ordered_stories) # usually 100

    cut_size = n_stories // 10
    n_stories -= n_stories % 10

    splits = [ordered_stories[i:i+cut_size] for i in range(0, n_stories, cut_size)]
    return splits


def draw_1(split):
    i = random.randint(0, 9)
    drawn = split.pop(i)
    return drawn, split


def mix_into_ten_folds(list_of_non_random_stories):
    # shuffle within section of similar sizes
    for s in list_of_non_random_stories:
        random.shuffle(s)

    # align size sections to form 10 sections that each contain 1 of each size bin
    list_of_non_random_stories = zip(*list_of_non_random_stories)

    # turn into lists to allow use of random.shuffle
    randomized_stories = [list(tup) for tup in list_of_non_random_stories]

    # shuffle again to make sure an item of random size makes it into dev and test
    for stories in randomized_stories:
        random.shuffle(stories)

    return randomized_stories


# Fan split helpers

def strip_totally(s):
    if isinstance(s, list):
        s = " ".join(s)

    regex = re.compile('[^a-zA-Z]')
    return regex.sub('', s)


def match_set_to_basil(tokens, basil):
    # gathers ids of tokens in set
    set_us = []

    for s in list(tokens):
        if s in basil:
            u = basil[s]
            set_us.append(u)
            basil.pop(s)
            tokens.pop(s)

    if len(tokens) > 0:
        handmade_mapping = {'TheFBIdirectorshouldbedrawnfromtheranksofcareerlawenforcementprosecutorsortheFBIitselfnotpoliticiansDurbintoldHuffPostlater': ['28hpo21'],
                            'ImonlyinterestedinLibyaifwegettheoilTrumpsaid': ['84fox26', '84hpo24'],
                            'HisconductisunbecomingofamemberofCongress': ['48nyt14','48fox12'],
                            'Iamtryingtosavelivesandpreventthenextterroristattack': ['64hpo6', '64nyt15'],
                            'Andyouexemplifyit': ['73hpo4', '73nyt2'],
                            'AndwhenyoursonlooksatyouandsaysMommalookyouwonBulliesdontwin': ['63nyt22', '63fox12'],
                            'Todaysrulingprovidescertaintyandclearcoherenttaxfilingguidanceforalllegallymarriedsamesexcouplesnationwide': ['66fox6', '66nyt7'],
                            'EricisagoodfriendandIhavetremendousrespectforhim': ['83fox8', '83hpo8'],
                            'ThecampaignandthestatepartyintendtocooperatewiththeUSAttorneysofficeandthestatelegislativecommitteeandwillrespondtothesubpoenasaccordingly': ['97fox4', '97hpo4'],
                            'AmericansdontbelievetheirleadersinWashingtonarelisteningandnowisthetimetochangethat': ['83fox4', '83fox12'],
                            'Icanthelpbutthinkthatthoseremarksarewellovertheline': ['50fox11', '50nyt6'],
                            'FaithmadeAmericastrongItcanmakeherstrongagain': ['87hpo4', '87nyt6'],
                            'ThefinalwordingwontbereleaseduntiltheNAACPsnationalboardofdirectorsapprovestheresolutionduringitsmeetinginOctober': ['42HPO7', '42FOX15'],
                            'Heobtainedatleastfivemilitarydefermentsfromtoandtookrepeatedstepsthatenabledhimtoavoidgoingtowaraccordingtorecords': ['73hpo7', '73nyt5'],
                            'Nomatterhowintrusiveandpartisanourpoliticscanbecomethisdoesnotjustifyapoorresponse': ['48nyt3', '48hpo42'],
                            'Itsaidsomethingcouldevolveandbecomemoredangerousforthatsmallpercentageofpeoplethatreallythinkourcountryhasbeentakenawayfromthem': ['42FOX17', '42HPO9']}
        for t in tokens:
            try:
                set_us.extend(handmade_mapping[t])
            except:
                pass #print(t)
    set_us = [s.lower() for s in set_us]
    return set_us


# helpers for both

def load_basil():
    fp = 'data/basil.csv'
    basil_df = pd.read_csv(fp, index_col=0).fillna('')
    basil_df.index = [el.lower() for el in basil_df.index]
    basil_df = basil_df.rename({'bias': 'label'})
    return basil_df


def load_basil_w_tokens():
    fp = 'data/basil_w_tokens.csv'
    basil_df = pd.read_csv(fp, index_col=0).fillna('')
    basil_df.index = [el.lower() for el in basil_df.index]
    return basil_df


def collect_sent_ids(set_type_stories, sent_by_story):
    set_type_sent_ids = []
    for story in set_type_stories:
        if story in sent_by_story:
            sent_ids = sent_by_story[story]
            set_type_sent_ids.extend(sent_ids)
    return set_type_sent_ids


class BergSplit:
    def __init__(self, split_input, split_dir='data/splits/berg_split', subset=1.0):
        split_fn = 'split.json'
        self.split_fp = os.path.join(split_dir, split_fn)
        self.split_input = split_input
        self.basil = load_basil().sample(frac=subset)

    def create_split_old(self, n_voters, sv):
        # order stories from most to least sentences in a story
        ordered_stories = order_stories(self.basil)

        # make ten cuts
        list_of_non_random_stories = cut_in_ten(ordered_stories)

        # mix them up
        ten_folds = mix_into_ten_folds(list_of_non_random_stories)

        # now there's 10 folds of each 10 stories
        fold_orders = [[0, 1, 2, 3, 4, 5, 6, 7, 8, 9],
                       [1, 2, 3, 4, 5, 6, 7, 8, 9, 0],
                       [2, 3, 4, 5, 6, 7, 8, 9, 0, 1],
                       [3, 4, 5, 6, 7, 8, 9, 0, 1, 2],
                       [4, 5, 6, 7, 8, 9, 0, 1, 2, 3],
                       [5, 6, 7, 8, 9, 0, 1, 2, 3, 4],
                       [6, 7, 8, 9, 0, 1, 2, 3, 4, 5],
                       [7, 8, 9, 0, 1, 2, 3, 4, 5, 6],
                       [8, 9, 0, 1, 2, 3, 4, 5, 6, 7],
                       [9, 0, 1, 2, 3, 4, 5, 6, 7, 8],
                       ]

        folds_in_ten_orders = []
        for fold_order in fold_orders:
            order_of_ten_folds = [ten_folds[fold_i] for fold_i in fold_order]
            folds_in_ten_orders.append(order_of_ten_folds)

        # now there's ten permutations of the ten folds
        stories_split_ten_ways = []
        for ordered_folds in folds_in_ten_orders:
            train_stories = []
            train_stories_list = ordered_folds[:8]
            for s in train_stories_list:
                train_stories.extend(s)
            dev_stories = ordered_folds[8]
            test_stories = ordered_folds[9]
            stories_split_one_way = {'train': [train_stories], 'dev': [dev_stories], 'test': test_stories}
            stories_split_ten_ways.append(stories_split_one_way)

        splits_json = {str(split_i): one_split for split_i, one_split in enumerate(stories_split_ten_ways)}
        with open(self.split_fp, 'w') as f:
            string = json.dumps(splits_json)
            f.write(string)

        return splits_json

    def create_split(self, n_voters, sv):
        # order stories from most to least sentences in a story
        random.seed(sv)

        ordered_stories = order_stories(self.basil)

        # make ten cuts
        list_of_non_random_stories = cut_in_ten(ordered_stories)

        # mix them up
        ten_folds = mix_into_ten_folds(list_of_non_random_stories)

        # now there's 10 folds of each 10 stories
        fold_orders = [[0, 1, 2, 3, 4, 5, 6, 7, 8, 9],
                       [1, 2, 3, 4, 5, 6, 7, 8, 9, 0],
                       [2, 3, 4, 5, 6, 7, 8, 9, 0, 1],
                       [3, 4, 5, 6, 7, 8, 9, 0, 1, 2],
                       [4, 5, 6, 7, 8, 9, 0, 1, 2, 3],
                       [5, 6, 7, 8, 9, 0, 1, 2, 3, 4],
                       [6, 7, 8, 9, 0, 1, 2, 3, 4, 5],
                       [7, 8, 9, 0, 1, 2, 3, 4, 5, 6],
                       [8, 9, 0, 1, 2, 3, 4, 5, 6, 7],
                       [9, 0, 1, 2, 3, 4, 5, 6, 7, 8],
                       ]

        folds_in_ten_orders = []
        for fold_order in fold_orders:
             order_of_ten_folds = [ten_folds[fold_i] for fold_i in fold_order]
             folds_in_ten_orders.append(order_of_ten_folds)

        # now there's ten permutations of the ten folds

        stories_split_ten_ways = []
        for ordered_folds in folds_in_ten_orders:
            test_stories = ordered_folds[-1]
            remaining_folds = ordered_folds[:-1]

            train_voters = []
            dev_voters = []
            for dev_i in range(n_voters):
                dev_stories = remaining_folds[dev_i]

                train_stories = []
                for i, fold in enumerate(remaining_folds):
                    if i != dev_i:
                        train_stories.extend(fold)

                train_voters.append(train_stories)
                dev_voters.append(dev_stories)

            stories_split_one_way = {'train': train_voters, 'dev': dev_voters, 'test': test_stories}
            stories_split_ten_ways.append(stories_split_one_way)

        splits_json = {str(split_i): one_split for split_i, one_split in enumerate(stories_split_ten_ways)}
        with open(self.split_fp, 'w') as f:
            string = json.dumps(splits_json)
            f.write(string)

        return splits_json

    def load_berg_story_split(self, recreate, n_voters, sv):
        if not os.path.exists(self.split_fp) or recreate:
            self.create_split(n_voters, sv)

        with open(self.split_fp, 'r') as f:
            return json.load(f)

    def map_stories_to_sentences(self):
        by_st = self.basil.groupby('story')
        sent_by_story = {n: gr.index.to_list() for n, gr in by_st}
        return sent_by_story

    def return_split_old(self, recreate, n_voters, sv):
        """ Returns list of folds and the sentence ids associated with their set types.
        :return: list of dicts with keys "train", "dev" & "test" and associated sentence ids.
        """
        # ...
        story_split = self.load_berg_story_split(recreate=recreate, n_voters=n_voters, sv=sv)

        sent_by_story = self.map_stories_to_sentences()

        splits_w_sent_ids = []
        for split_i, stories_split_one_way in story_split.items():
            split_sent_ids = {}

            for set_type in ['train', 'dev', 'test']:
                set_type_stories = stories_split_one_way[set_type]

                set_type_sent_ids = []
                for story in set_type_stories:

                    if story in sent_by_story:
                        sent_ids = sent_by_story[story]
                        set_type_sent_ids.extend(sent_ids)

                if set_type != 'test':
                    set_type_sent_ids = [set_type_sent_ids, set_type_sent_ids]

                split_sent_ids[set_type] = set_type_sent_ids

            splits_w_sent_ids.append(split_sent_ids)

        return splits_w_sent_ids

    def return_split(self, recreate, n_voters, sv):
        """ Returns list of folds and the sentence ids associated with their set types.
        :return: list of dicts with keys "train", "dev" & "test" and associated sentence ids.
        """
        # ...
        story_split = self.load_berg_story_split(recreate=recreate, n_voters=n_voters, sv=sv)

        sent_by_story = self.map_stories_to_sentences()

        splits_w_sent_ids = []
        for split_i, stories_split_one_way in story_split.items():
            split_sent_ids = {}

            test_stories = stories_split_one_way['test']
            test_sent_ids = collect_sent_ids(test_stories, sent_by_story)
            split_sent_ids['test'] = test_sent_ids

            all_train_sent_ids = []
            all_dev_sent_ids = []
            for v in range(n_voters):
                train_stories = stories_split_one_way['train'][v]
                train_sent_ids = collect_sent_ids(train_stories, sent_by_story)
                all_train_sent_ids.append(train_sent_ids)

                dev_stories = stories_split_one_way['dev'][v]
                dev_sent_ids = collect_sent_ids(dev_stories, sent_by_story)
                all_dev_sent_ids.append(dev_sent_ids)

            split_sent_ids['train'] = all_train_sent_ids
            split_sent_ids['dev'] = all_dev_sent_ids

            splits_w_sent_ids.append(split_sent_ids)

        return splits_w_sent_ids


class FanSplit:
    def __init__(self, split_input, split_dir, subset=1.0):
        self.split_input = split_input
        self.basil = load_basil_w_tokens().sample(frac=subset)
        self.split_dir = split_dir

    def load_fan_tokens(self, setname):
        with open(self.split_dir + '/' + setname + '_tokens.txt', encoding='utf-8') as f:
            toks = [el.strip() for el in f.readlines()]
        return toks

    def match_fan(self):
        basil = self.basil
        basil['split'] = 'train'
        basil['for_matching'] = basil.tokens.apply(strip_totally)

        basil_for_matching = {s: u for s, u in zip(basil.for_matching.values, basil.index.values)}
        sents = []
        for sn in ['train', 'val', 'test']:
            tokens = self.load_fan_tokens(sn)
            tokens = {strip_totally(s): None for s in tokens}
            us = match_set_to_basil(tokens, basil_for_matching)
            sents.append(us)
        train_sents, dev_sents, test_sents = sents
        return train_sents, dev_sents, test_sents

    def return_split(self):
        """ Returns list of folds and the sentence ids associated with their set types.
        :return: list of dicts with keys "train", "dev" & "test" and associated sentence ids.
        """
        train_sents, dev_sents, test_sents = self.match_fan()
        #return [{'train': train_sents, 'dev': dev_sents, 'test': test_sents}]
        return [{'train': [train_sents], 'dev': [dev_sents], 'test': test_sents}]


class Split:
    def __init__(self, input_dataframe, which='berg', split_loc='data/splits/', tst=False, subset=1.0, recreate=False,
                 n_voters=1, sv=99):
        """
        Splits input basil-like dataframe into folds.

        :param input_dataframe: dataframe with at least all the same fields as basil_raw
        :param which: string specifying whether fan split or own split should be used
        """
        assert isinstance(input_dataframe, pd.DataFrame)

        self.input_dataframe = input_dataframe
        self.which = which
        self.tst = tst
        self.n_voters = n_voters

        if self.which == 'fan':
            splitter = FanSplit(input_dataframe, subset=subset, split_dir=os.path.join(split_loc, 'fan_split'))
            self.spl = splitter.return_split()

        elif self.which == 'berg':
            splitter = BergSplit(input_dataframe, subset=subset, split_dir=os.path.join(split_loc, 'berg_split'))
            self.spl = splitter.return_split(recreate, n_voters=n_voters, sv=sv)

        elif self.which == 'both':
            fan_splitter = FanSplit(input_dataframe, subset=subset, split_dir=os.path.join(split_loc, 'fan_split'))
            berg_splitter = BergSplit(input_dataframe, subset=subset, split_dir=os.path.join(split_loc, 'berg_split'))
            fan_spl = fan_splitter.return_split()
            berg_spl = berg_splitter.return_split(recreate, n_voters, sv=sv)
            self.spl = fan_spl + berg_spl

    def apply_split(self, features):
        """
        Applies nr of folds and order of fold content to the input dataframe.

        :param features: whether you want tokens, embeddings, or other from basil dataset
        :return: (dict) a list of folds,
         each a dict of set types (train, dev, test) containing slice of input df
        """
        empty_folds = self.spl

        filled_folds = []
        for i, empty_fold in enumerate(empty_folds):

            # if bias -> label renaming not executed in other scripts, fix it here
            if 'label' not in self.input_dataframe.columns:
                if 'bias' in self.input_dataframe.columns:
                    print('Please replace basil column name "bias" with "label."')
                    self.input_dataframe.rename({'bias': 'label'})

            # oversample
            # pos_cases = self.input_dataframe[self.input_dataframe.label == 1]
            # pos_cases = pd.concat([pos_cases]*5)
            # self.input_dataframe = pd.concat([self.input_dataframe, pos_cases])

            train_sent_ids = empty_fold['train']
            dev_sent_ids = empty_fold['dev']
            test_sent_ids = empty_fold['test']

            if 'label' not in features:
                features += ['label']

            '''
            existing_ids = self.input_dataframe.index.tolist()
            existing_ids = set(self.input_dataframe.index.tolist())
            all = test_sent_ids + train_sent_ids[0] + dev_sent_ids[0]
            test_sent_ids = set(test_sent_ids)
            train_sent_ids = set(train_sent_ids[0])
            dev_sent_ids = set(dev_sent_ids[0])
            all = set(all)
            diff = [el for el in test_sent_ids if el not in existing_ids]
            print(len(diff), diff)
            '''
            #print(features, self.input_dataframe.columns)

            test_df = self.input_dataframe.loc[test_sent_ids, features ] #+ ['label']

            train_dfs = []
            dev_dfs = []
            # for v in range(len(empty_fold['train'])):
            for v in range(self.n_voters):
                train_sent_ids = empty_fold['train'][v]
                dev_sent_ids = empty_fold['dev'][v]
                train_df = self.input_dataframe.loc[train_sent_ids, :]

                train_df = self.input_dataframe.loc[train_sent_ids, features ] #+ ['label']
                dev_df = self.input_dataframe.loc[dev_sent_ids, features ] #+ ['label']

                train_dfs.append(train_df)
                dev_dfs.append(dev_df)

            '''
            train_df = self.input_dataframe.loc[train_sent_ids, :]
            train_df = self.input_dataframe.loc[train_sent_ids, features + ['label']]
            dev_df = self.input_dataframe.loc[dev_sent_ids, features + ['label']]
            '''

            #train_X, train_y = train_df[features], train_df.label
            #dev_X, dev_y = dev_df[features], dev_df.label
            #test_X, test_y = test_df[features], test_df.label

            if self.which == 'fan':
                name = 'fan'
            elif self.which == 'berg':
                name = i+1
            elif self.which == 'both':
                name = 'fan' if i == 0 else i

            filled_fold = {'train': train_dfs,
                           'dev': dev_dfs,
                           'test': test_df,
                           'sizes': (len(train_df), len(dev_df), len(test_df)),
                           'name': name}

            #print("Label distribution of fold:", filled_fold['name'])
            #print(train_df.label.value_counts(normalize=0))
            #print(dev_df.label.value_counts())
            #print(test_df.label.value_counts())

            filled_folds.append(filled_fold)

        return filled_folds


def split_input_for_bert(data_dir, recreate, n_voters, sv):
    ''' This function loads basil, selects those columns which are relevant for creating input for finetuning BERT to
    our data, and saves them for each berg-fold seperately. '''

    # load basil data with BERT-relevant columns
    basil_infp = os.path.join(data_dir, 'basil.csv')
    data = pd.read_csv(basil_infp, index_col=0, names=['id', 'label', 'alpha', 'sentence'])
    data.index = [el.lower() for el in data.index]

    # write data with only these columns to all.tsv
    data.to_csv(data_dir + f"/all.tsv", sep='\t', index=False, header=False)

    # write data into folds
    spl = Split(data, which='berg', recreate=recreate, n_voters=n_voters, sv=sv)
    folds = spl.apply_split(features=['id', 'label', 'alpha', 'sentence'])

    # write data for each fold with only BERT-relevant columns to all.tsv
    for i, fold in enumerate(folds):
        test_ofp = os.path.join(data_dir, f"{fold['name']}_test.tsv")
        if not os.path.exists(test_ofp) or recreate:

            pos_cases = fold['test'].label.value_counts().loc[1]
            total = len(fold['test']['label'])
            print(f'Fold {i+1} Biased instances: \n {pos_cases / total * 100}')

            fold['test'].to_csv(test_ofp, sep='\t', index=False, header=False)

        for v in range(n_voters):
            train_ofp = os.path.join(data_dir, f"{fold['name']}_{v}_train.tsv")
            dev_ofp = os.path.join(data_dir, f"{fold['name']}_{v}_dev.tsv")

            if not os.path.exists(train_ofp) or recreate:
                fold['train'][v].to_csv(train_ofp, sep='\t', index=False, header=False)

            if not os.path.exists(dev_ofp) or recreate:
                fold['dev'][v].to_csv(dev_ofp, sep='\t', index=False, header=False)

    return folds