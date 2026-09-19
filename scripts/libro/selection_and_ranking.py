import math
import os
import random
import tokenize
import numpy as np
import pandas as pd
import argparse
import difflib
import ast

from collections import defaultdict
from tqdm import tqdm

from scripts.config import ROOT_DIR
from scripts.libro.parse_bug_report import load_bug_report_features
from scripts.utils.common import count_assertions, process_result, count_test_tokens
from scripts.utils.normalize_utils import normalize_test, replace_code, replace_memory_address
from scripts.libro.process_failure_output import *
# from scripts.libro._process_bug_report import *

RESULT_PATH = f'{ROOT_DIR}/results/{{dataset}}/{{exp_name}}.json'
GEN_TEST_PATH = f'{ROOT_DIR}/data/{{dataset}}/gen_tests_{{exp_name}}/'

BIG_NUMBER = 100000
SELECTION_THRESHOLD = 1

def select_confident_bugs(rank_feature_df, threshold=1):
    max_df = rank_feature_df.groupby('bug_id').max()
    df = max_df[max_df.clus_size_output_fib <= threshold]
    selected_df = max_df[max_df.clus_size_output_fib > threshold].reset_index()
    selected_bugs = list(selected_df.bug_id.unique())

    return rank_feature_df[rank_feature_df.bug_id.isin(selected_bugs)]


def rank_tests_using_clusters(rank_feature_df, test_clusters, random_baseline=False, seed=0):
    rows = []
    for bug_id, fib_tests_features in rank_feature_df.groupby('bug_id'):
        if random_baseline:
            sorted_fib_tests = shuffle_fib_tests(fib_tests_features, seed=seed)
        else:
            sorted_fib_tests = sort_unique_fib_tests(bug_id, fib_tests_features, test_clusters)

        success_tests = fib_tests_features[fib_tests_features.success].test_path.tolist()

        success_ranks = []
        for i, (test, score_first, score_second, score_third) in enumerate(sorted_fib_tests):
            if test in success_tests:
                success_ranks.append(i+1)    

        rows.append({
            'bug_id': bug_id,
            'total_success_fibs': len(success_tests),
            'first_success_rank': min(success_ranks) if len(success_ranks) > 0 else BIG_NUMBER,
            'success_ranks': success_ranks,
            'num_clusters': len(sorted_fib_tests),
            'score_first': score_first,
            'score_second': score_second,
            'score_third': score_third,
            'sorted_tests': [os.path.basename(test[0]) for test in sorted_fib_tests]
        })
        
    
    return pd.DataFrame(rows)


def collect_ranking_features(fib_bug_ids, fib_clusters, aggreement_scores, bug_report_features, parsed_output):
    rows = []
    success_count = 0
    # with open("/home/ubuntu/libro/scripts/check_reproduced/result.json") as f:
    #     reproduce_result = json.load(f)
    for bug_id in fib_bug_ids:
        has_success = False
        for rep, test_paths in fib_clusters[bug_id].items():
            rep_test = test_paths[0]
            rep_test = os.path.basename(rep_test)
            features = {
                'bug_id': bug_id,
                'test_path': rep_test,
                'success': result_dict[bug_id][rep_test]['success'],
            }

            if features['success']:
                has_success = True

            test_content = None
            with open(test_paths[0]) as f:
                test_content = f.read().strip()
                try:
                    features['test_length'] = count_test_tokens(test_content.strip())
                except tokenize.TokenError as e:
                    print(test_paths[0])
                    raise e
                try:
                    features['num_assertions'] = count_assertions(test_content)
                except IOError:
                    features['num_assertions'] = 0 # If file read fails, set num_assertions to 0

            features[f'clus_size_output_fib'] = aggreement_scores[bug_id][rep_test]

            features[f'num_fib_tests'] = len(test_paths)
            match_output_result = match_buggy_output_w_report(parsed_output[bug_id][rep_test], bug_report_features[bug_id])
            # match_test_result = match_test_body_w_report(test_content, bug_report_features[bug_id])

            features['traceback_match'] = match_output_result['traceback_match']
            features['exception_type_match'] = match_output_result['exception_type_match']
            # features['test_exception_type_match'] = match_test_result['exception_type_match']
            # features['reproduced'] = reproduce_result[bug_id][rep_test]['reproduce']
            
            rows.append(features)

        if has_success:
            success_count += 1    

    return pd.DataFrame(rows)

def sort_unique_fib_tests(bug_id, test_features, test_clusters):
    test_features = test_features.set_index('test_path')
    WEIGHTS = {
        'size': 0.3,    # Weight of cluster size
        'length': 0.5,  # Weight of average test length
        'asserts': 0.2  # Weight of average number of assertions
    }

    # Step 1: Preprocessing, calculate raw features for each cluster
    cluster_raw_features = {}
    for c, test_paths in test_clusters[bug_id].items():
        if not test_paths: continue

        # Calculate features for each test
        test_details = []
        test_keys = []
        for test_path in test_paths:
            test_key = os.path.basename(test_path)
            if test_key not in test_features.index: continue
            test_keys.append(test_key)

            test_details.append({
                'key': test_key,
                'length': test_features.loc[test_key].test_length,
                'assertions': test_features.loc[test_key].num_assertions,
                'match': int(test_features.loc[test_key].exception_type_match)
            })

        if not test_details: continue
        if len(test_keys) == 0: continue
        # Calculate aggregate features for the cluster
        test_feat_rep = test_features.loc[test_keys[0]] # Use the first one as representative
        bug_report_match = int(test_feat_rep.exception_type_match) + 2 * int(test_feat_rep.traceback_match)
        
        cluster_raw_features[c] = {
            'bug_report_match': bug_report_match,
            'size': len(test_details),
            'min_length': np.min([t['length'] for t in test_details]),
            'min_assertions': np.min([t['assertions'] for t in test_details]),
            'tests': test_details # Save detailed information for all tests in the cluster
        }

    # Step 2: Define scoring and ranking functions
    def get_sorted_clusters(cluster_keys, all_features):
        if not cluster_keys:
            return []

        group_features = [all_features[k] for k in cluster_keys]
        
        # Calculate the maximum value of the current group for normalization
        max_size = max(f['size'] for f in group_features) or 1
        max_length = max(f['min_length'] for f in group_features) or 1
        max_assertions = max(f['min_assertions'] for f in group_features) or 1
        
        cluster_scores = {}
        for k in cluster_keys:
            feat = all_features[k]
            
            # Use log1p (i.e., log(1+x)) to avoid log(0)
            score_size = 100 * (math.log1p(feat['size']) / math.log1p(max_size)) if max_size > 0 else 0
            score_len = 100 * (1 - math.log1p(feat['min_length']) / math.log1p(max_length)) if max_length > 0 else 100
            score_assert = 100 * (1 - math.log1p(feat['min_assertions']) / math.log1p(max_assertions)) if max_assertions > 0 else 100
            
            total_score = (WEIGHTS['size'] * score_size +
                           WEIGHTS['length'] * score_len +
                           WEIGHTS['asserts'] * score_assert)
            
            cluster_scores[k] = total_score
            
        # Sort cluster keys in descending order based on total score
        return sorted(cluster_keys, key=lambda k: cluster_scores[k], reverse=True)

    # Step 3: Group and rank separately
    matching_keys = [k for k, v in cluster_raw_features.items() if v['bug_report_match'] > 0]
    non_matching_keys = [k for k, v in cluster_raw_features.items() if v['bug_report_match'] == 0]
    
    sorted_matching_keys = get_sorted_clusters(matching_keys, cluster_raw_features)
    sorted_non_matching_keys = get_sorted_clusters(non_matching_keys, cluster_raw_features)
    
    # Merge sorted keys
    sorted_cluster_keys = sorted_matching_keys + sorted_non_matching_keys

    # Step 4: Generate final list
    final_deliver_list = []
    for c_key in sorted_cluster_keys:
        # Also rank tests within the cluster
        tests_in_cluster = cluster_raw_features[c_key]['tests']
        # Ranking rule: match > assertions > length
        tests_in_cluster.sort(key=lambda t: (-t['match'], t['assertions'], t['length']))
        
        # Only extract necessary information like key
        final_deliver_list.extend([(t['key'], t['length'], t['match'], t['assertions']) for t in tests_in_cluster])

    return final_deliver_list


def shuffle_fib_tests(fib_test_features, seed):
    random_test_order = []

    for bug_id, test_feat in fib_test_features.iterrows():
        crash_type_match_score = int(test_feat.exception_type_match and test_feat.is_crash)
        actual_value_match_score = int(test_feat.actual_value_match and not test_feat.is_crash)
        output_cluster_size_score = test_feat.clus_size_output_fib

        random_test_order.append((test_feat.test_path, crash_type_match_score + actual_value_match_score, output_cluster_size_score, test_feat.test_path))
        
    random.Random(seed).shuffle(random_test_order)
    return random_test_order


# def match_test_body_w_report(test_content, bug_report):
    
#     return {
#         'handled_exception_type': None,
#         'exception_type_match': False
#     }


def match_buggy_output_w_report(parsed_output, bug_report):
    # Bug Report: traceback, error_message
    # Buggy Output: traceback, error_message(type, msg)
    exception_type_match = False
    traceback_match = False

    # bug_tracebacks: list[
        # {
            # stacktrace: str,
            # error_message: {
                # error_message: str,
                # error_type: str,
                # error_content: str
            # }
        # }
    # ]
    bug_tracebacks = bug_report['tracebacks']
    
    # bug_error_messages: list[
        # {
            # error_message: str,
            # error_type: str,
            # error_content: str
        # }
    # ]
    bug_error_messages = bug_report['error_messages']
    
    output_tracebacks = parsed_output['tracebacks']
    output_error_messages = parsed_output['exceptions']
    
    if len(bug_tracebacks) == 0 and len(bug_error_messages) == 0:
        # If no traceback or error in bug report, check for AssertionError
        for error in output_error_messages:
            if 'AssertionError' in error['error_type'] or 'Failed' in error['error_type']:
                exception_type_match = True
                break
    
    # 1. match tracebacks
    for i, output_tb in enumerate(output_tracebacks):
        for j, bug_tb in enumerate(bug_tracebacks):
            similarity = difflib.SequenceMatcher(None, output_tb['stack_trace'], bug_tb['stack_trace']).ratio()
            if similarity > 0.8:
                traceback_match = True
                break
        if traceback_match:
            break

    # 2. match error messages
    for i, output_error in enumerate(output_error_messages):
        for j, bug_error in enumerate(bug_error_messages):
            if output_error['error_type'] == bug_error['error_type']:
                exception_type_match = True
                break
        if exception_type_match:
            break
    return {
        'traceback_match': traceback_match,
        'exception_type_match': exception_type_match,
    }


def cluster_tests(bug_result, among_fib=True, by='syntax', dataset='d4j'):
    """
    Given test results for a bug, cluster tests either by test syntax or by output value
    - among_fib: True | False
    - by: syntax | output
    """
    clusters = defaultdict(list)
    targets = []

    for test_result in bug_result.values():
        if test_result['parse_error']:
            continue

        if among_fib and not test_result['is_fib']:
            continue

        if by == 'syntax':
            with open(test_result['test_file_path']) as f:
                gen_test = f.read()
            try:
                rep = normalize_test(gen_test)
            except SyntaxError as e:
                print(f'Syntax error in test {test_result["test_file_path"]}: {e}')
                raise e
        elif by == 'output':
            if not test_result['fib_test_id']:
                test_name = None
                test_path = None
                fib_test_id = None
            elif '::' in test_result['fib_test_id']:
                testid_name = test_result['fib_test_id'].split(' ')[0].split('::')
                fib_test_id = testid_name[0]
                test_name = testid_name[1] if len(testid_name) > 1 else None
                test_path = None
            else:
                test_name = test_result['fib_test_id'].split(' ')[0]
                fib_test_id = test_result['fib_test_id'].split(')')[0].split('(')[-1]
                test_path = fib_test_id.split('.')[:-1]
                test_path = '/'.join(test_path)
            if test_result['buggy_output'] is None:
                continue
            
            # TODO: Need to handle the code in the output
            with open(test_result['test_file_path']) as f:
                gen_test = f.read()
                gen_test = gen_test.strip()
            rep_lines = replace_code(gen_test, test_result['buggy_output'].split('\n'))
            rep_lines = replace_memory_address(rep_lines)
            rep = '\n'.join(rep_lines)
            if fib_test_id is not None:
                rep = rep.replace(fib_test_id, '[FIB_TEST_ID]')
            if test_name is not None:
                rep = rep.replace(test_name, '[FIB_TEST_NAME]')
            if test_path is not None:
                rep = rep.replace(test_path, '[FIB_TEST_PATH]')

        clusters[rep].append(test_result['test_file_path'])
        targets.append(test_result['test_file_path'])

    if len(clusters) == 0:
        for test_result in bug_result.values():
            if test_result['parse_error']:
                continue

            if by == 'syntax':
                with open(test_result['test_file_path']) as f:
                    gen_test = f.read()
                try:
                    rep = normalize_test(gen_test)
                except SyntaxError as e:
                    print(f'Syntax error in test {test_result["test_file_path"]}: {e}')
                    raise e
            elif by == 'output':
                if not test_result['fib_test_id']:
                    test_name = None
                    test_path = None
                    fib_test_id = None
                elif '::' in test_result['fib_test_id']:
                    testid_name = test_result['fib_test_id'].split(' ')[0].split('::')
                    fib_test_id = testid_name[0]
                    test_name = testid_name[1] if len(testid_name) > 1 else None
                    test_path = None
                else:
                    test_name = test_result['fib_test_id'].split(' ')[0]
                    fib_test_id = test_result['fib_test_id'].split(')')[0].split('(')[-1]
                    test_path = fib_test_id.split('.')[:-1]
                    test_path = '/'.join(test_path)
                if test_result['buggy_output'] is None:
                    continue
                
                # TODO: Need to handle the code in the output
                with open(test_result['test_file_path']) as f:
                    gen_test = f.read()
                    gen_test = gen_test.strip()
                rep_lines = replace_code(gen_test, test_result['buggy_output'].split('\n'))
                rep_lines = replace_memory_address(rep_lines)
                rep = '\n'.join(rep_lines)
                if fib_test_id is not None:
                    rep = rep.replace(fib_test_id, '[FIB_TEST_ID]')
                if test_name is not None:
                    rep = rep.replace(test_name, '[FIB_TEST_NAME]')
                if test_path is not None:
                    rep = rep.replace(test_path, '[FIB_TEST_PATH]')

            clusters[rep].append(test_result['test_file_path'])
            targets.append(test_result['test_file_path'])
    return clusters

def aggregate_results_from_random_baseline(rank_feature_df_selected, test_clusters):
    seeds = range(100)
    result = {}
    Ns = [1, 3, 5]

    rdf = rank_feature_df_selected

    wasted_effort_results = defaultdict(list)
    acc_results = defaultdict(list)

    for s in tqdm(seeds):
        df = rank_tests_using_clusters(rdf, test_clusters, random_baseline=True, seed=s)
        total = len(df)
        
        for N in Ns:
            rows = []
            for i, row in df.iterrows():
                first_success = row['first_success_rank']
                num_clusters = row['num_clusters']
                if first_success <= N:
                    wasted_effort = first_success - 1
                elif first_success > N:
                    wasted_effort = min(num_clusters, N)

                rows.append({
                    'bug_id': row['bug_id'],
                    'success': wasted_effort < min(num_clusters, N),
                    'wasted_effort': wasted_effort
                })
            
            result[N] = pd.DataFrame(rows)

        for N in Ns:
            wasted_effort_results[N].append((float(result[N].wasted_effort.sum()), float(result[N].wasted_effort.mean())))
            acc_results[N].append(float(result[N].success.sum()))


        list_acc = []
        list_wef = []
        for i, row in df.iterrows():
            first_success = row['first_success_rank']
            num_clusters = row['num_clusters']
            if first_success <= num_clusters:
                wasted_effort = first_success - 1
            elif first_success == BIG_NUMBER:
                wasted_effort = num_clusters
            else:
                raise Exception('first success rank is bigger than number of the clusters')

            list_acc.append(wasted_effort < num_clusters)
            list_wef.append(wasted_effort)

    aggr_result = {}

    for N in Ns:
        aggr_result[N] = {
            'wefs': wasted_effort_results[N],
            'acc': acc_results[N]
        }

    return aggr_result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('--exp_name', default='final', help='swe, zeroshot, enhance')
    parser.add_argument('-f', '--result_file', default=None, help='Path to the execution result file (e.g., `../results/example2_n50.json`)')
    parser.add_argument('-g', '--gen_test_path', default=None, help='Directory that contains raw generated tests (e.g., `../data/Defects4J/gen_tests/`)')
    parser.add_argument('--random', action='store_true', help='Produce random baseline results')
    parser.add_argument('--swe', default='swe', help='swe, swt, tdd')
    parser.add_argument('--model', default='deepseek-chat', help='Experiment name to be used in the result path')
    args = parser.parse_args()

    if args.exp_name:
        result_path = os.path.expanduser(RESULT_PATH.format(dataset=args.exp_name, exp_name=args.model))
        gen_test_path = os.path.expanduser(GEN_TEST_PATH.format(dataset=args.exp_name, exp_name=args.model))
        dname = args.exp_name
    else:
        raise Exception('Invalid dataset')

    if args.result_file is not None:
        print(f'Use the custom result file {args.result_file}...')
        result_path = args.result_file

    if args.gen_test_path is not None:
        print(f'Use the custom generated test path {args.gen_test_path}...')
        gen_test_path = args.gen_test_path
        
    if args.swe is not None:
        print(f'Use the custom swe type {args.swe}...')
        if args.swe == 'swe':
            with open('swe.txt') as f:
                all_bugs = [e.strip() for e in f.readlines() if e.strip() != '']
        elif args.swe == 'swt':
            with open('swt.txt') as f:
                all_bugs = [e.strip() for e in f.readlines() if e.strip() != '']
        elif args.swe == 'tdd':
            with open('tdd.txt') as f:
                all_bugs = [e.strip() for e in f.readlines() if e.strip() != '']
        print(f'All bugs: {len(all_bugs)}')
        
        
    result_dict = process_result(result_path, gen_test_path, all_bugs)

    # 1. converts test results into dataframe
    rows = []
    for bug_id, test_results in result_dict.items():
        for filename, test_result in test_results.items():
            rows.append({
                'bug_id': bug_id,
                'test_key': filename,
                'file_path': test_result['test_file_path'],
                'parse_error': test_result['parse_error'],
                'compile_error': test_result['compile_error'],
                'FIB': test_result['is_fib'],
                'success': test_result['success'] if not test_result['has_error'] else False
            })

    result_df = pd.DataFrame(rows)
    # fib_bug_ids = result_df[result_df['FIB'] == True].bug_id.unique()
    fib_bug_ids = result_df.bug_id.unique()
    success_bug_ids = result_df[result_df['success'] == True].bug_id.unique()

    # 2. extract information from bug report and failure output for each bug
    bug_report_features = load_bug_report_features()
    parsed_output = defaultdict(dict)

    for bug_id in fib_bug_ids:
        for name, test_result in result_dict[bug_id].items():
            # print(name)
            if test_result['buggy_output'] is not None:
                proj = bug_id.split('__')[1].split('-')[0]
                # if len(test_result['buggy_output']) > 0:
                # print(f'Parsing buggy output for {bug_id}...')
                if test_result['buggy_output'] is None or len(test_result['buggy_output']) == 0:
                    parsed_output[bug_id][name] = None
                # print(f'Parsing buggy output for {bug_id} - {name}...')
                parsed_output[bug_id][name] = parse_buggy_output(proj, test_result['buggy_output'], mode='swe')

    # 3. group syntactically equivalent tests (syntax clusters)
    fib_clusters = {}
    for bug_id, bug_result in tqdm(result_dict.items()):
        if len(bug_result) == 0:
            continue
        fib_clusters[bug_id] = cluster_tests(bug_result, among_fib=True, by='syntax', dataset=dname)

    # 4. construct output clusters among FIBs
    test_clusters = dict()
    aggreement_scores = defaultdict(dict)

    for bug_id, bug_result in tqdm(result_dict.items()):
        clusters = cluster_tests(bug_result, among_fib=True, by='output', dataset=dname)
        test_clusters[bug_id] = clusters
        for c, test_paths in clusters.items():
            for test_path in test_paths:
                test_key = os.path.basename(test_path)
                aggreement_scores[bug_id][test_key] = len(test_paths)

    # 5. collect rank features and apply intra+inter cluster ranking strategy
    rank_feature_df = collect_ranking_features(fib_bug_ids, fib_clusters, aggreement_scores, bug_report_features, parsed_output)

    with open(f'{ROOT_DIR}/results/{dname}/ranking_features_{dname}_{args.model}.csv', 'w') as f:
        rank_feature_df.to_csv(f, index=False)

    rank_df = rank_tests_using_clusters(rank_feature_df, test_clusters)
    
    # with open(f'ranking_{dname}.csv', 'w') as f:
    #     rank_df.to_csv(f, index=False)

    with open(f'{ROOT_DIR}/results/{dname}/ranking_{dname}_{args.model}.csv', 'w') as f:
        rank_df[['bug_id', 'first_success_rank', 'total_success_fibs', 'num_clusters', 'sorted_tests']].to_csv(f, index=False)

    # result before selection
    print(f'\n[Ranking result before selection]')
    F2X_count = len(rank_df)
    Ns = [1, 3, 5]
    for N in Ns:
        F2P_count = len(rank_df[rank_df.first_success_rank <= N])
        print(f'* acc@{N}: {F2P_count}')
        print(f'* acc@{N} (F2P): {1.0 * F2P_count / len(all_bugs)}')
        if N == 1:
            with open(f'./tmp_data/{dname}_{args.model}_@1_acc.txt', 'w') as f:
                f.write(rank_df[rank_df.first_success_rank <= N]['bug_id'].to_csv(index=False))
        if N == 5:
            with open(f'./tmp_data/{dname}_{args.model}_@5_acc.txt', 'w') as f:
                f.write(rank_df[rank_df.first_success_rank <= N]['bug_id'].to_csv(index=False))
    print(f'* Total (success): {len(rank_df[rank_df.first_success_rank < BIG_NUMBER])}')
    print(f'* Total (fib): {len(rank_df)}')
    