import argparse
from pathlib import Path
from datasets import load_dataset
import glob
import os
import pandas as pd

from tqdm import tqdm
from scripts.libro.postprocess_swe import setup_environment
from scripts.test_retrieval.similarities.textual_similarity import get_semantic_similarity
from scripts.test_retrieval.utils import Test, get_function_calling_results
from scripts.utils.git_utils import git_reset, initialize
from scripts.utils.related_test_util import list_all_tests_in_file
from scripts.utils.swe_util import get_env_name, repo_path, swe_test_path_prefix
from scripts.test_retrieval.similarities.call_tree_similarity import build_gen_test_call_graph,  get_call_similarities

def retrieval(bug_report, gen_test_path, injection_path, topk, wo_call, p1, tree_path, keywords_path):
    # for each test in each file:
    initialize(bug_report)
    env_name = get_env_name(bug_report)
    bug_id = bug_report["instance_id"]
    print(f"Retrieving {bug_id}")
    proj = bug_report['repo']
    proj_path = repo_path(proj)
    with open(gen_test_path) as f:
        gen_test_content = f.read()
    
    if not wo_call:
        try:
            setup_environment(proj, proj_path, env_name)
            gen_test_use_tree, tree_builder = build_gen_test_call_graph(bug_report, gen_test_content, injection_path, tree_path, keywords_path)
            git_reset(proj_path)
            if gen_test_use_tree is None: # Syntax error in generated test, use only semantic similarity
                wo_call = True
        except Exception as e:
            print(f"Failed to setup environment for {bug_id}, skip call graph similarity. Error: {e}")
            # raise e
            wo_call = True
    if wo_call:
        topk = max(10, topk)
    function_calling_results = get_function_calling_results(bug_id, injection_path)
    function_calling_files = [r['file'] for r in function_calling_results]
    function_calling_tests = [r['name'] for r in function_calling_results]
   
    # 1. Collect all test objects
    test_dir = swe_test_path_prefix(proj, bug_id) + '**/*.py'
    test_dir = os.path.join(proj_path, test_dir)
    test_files = glob.glob(test_dir, recursive=True)
    test_files = [test_file for test_file in test_files if test_file.split('/')[-1].startswith('test') or test_file.split('/')[-1].startswith('unittest')]
    all_test_objs:list[Test] = []

    for test_file in tqdm(test_files):
        rel_path = test_file.replace(proj_path, '')
        tests = list_all_tests_in_file(test_file)
        
        for test, test_content in tests.items():
            class_name = ''
            test_name = test
            if '.' in test:
                class_name = test.split('.')[0]
                test_name = test.split('.')[1]
            test_obj = Test(proj, rel_path, class_name, test_name, test_content)

            # function calling result
            # Same test name / Same file
            if test_obj.test_name in function_calling_tests:
                test_obj.func_calling_test_match = True
            if test_obj.rel_file_path in function_calling_files:
                test_obj.func_calling_file_match = True

            all_test_objs.append(test_obj)

    # 2. Calculate semantic similarity
    print("Calculating semantic similarity...")
    semantic_similarities = get_semantic_similarity(gen_test_content, all_test_objs)

    df = pd.DataFrame(columns=['proj', 'file_path', 'class_name', 'test_name', 'name_similarity', 'bm25_similarity', 'semantic_similarity', 'call_graph_similarity', 'func_calling_file_match', 'func_calling_test_match', 'score'])

    for test_obj, semantic_similarity in zip(all_test_objs, semantic_similarities):
        assert isinstance(test_obj, Test)
        test_obj.semantic_similarity = semantic_similarity
        # test_obj.calc_final_score()
        if test_obj.semantic_similarity > 0:
            df.loc[len(df)] = test_obj.to_json()

    # sort the test cases by similarity
    semantic_top_df = df.sort_values(by='semantic_similarity', ascending=False)
    semantic_topk = semantic_top_df.iloc[:topk]
    # Take top 100
    topk_score = semantic_top_df.iloc[:100]['semantic_similarity'].min()
    if not wo_call:
        top_df = pd.DataFrame(columns=['proj', 'file_path', 'class_name', 'test_name', 'name_similarity', 'bm25_similarity', 'semantic_similarity', 'call_graph_similarity', 'func_calling_file_match', 'func_calling_test_match', 'score'])
        
        # Calculate call graph similarity
        test_objs = [t for t in all_test_objs if t.semantic_similarity >= topk_score]
        call_similarities = get_call_similarities(tree_builder, gen_test_use_tree, test_objs, p1)
        
        for test_obj, call_sim in zip(test_objs, call_similarities):
            assert isinstance(test_obj, Test)
            test_obj.call_graph_similarity = call_sim
            test_obj.calc_final_score()
            if test_obj.score > 0:
                top_df.loc[len(top_df)] = test_obj.to_json()
        
        # sort the test cases by similarity
        call_top_df = top_df.sort_values(by='call_graph_similarity', ascending=False)
        call_topk = call_top_df.iloc[:topk]

        score_top_df = top_df.sort_values(by='score', ascending=False)
        score_topk = score_top_df.iloc[:topk]
    else:
        score_topk = None
        call_topk = None
    # save the results
    return score_topk, semantic_topk, call_topk


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument("--gen_test_dir", type=str, default="./data/sketch/gen_tests_gpt-4o_1")
    parser.add_argument("--output_dir", type=str, default="./retrieval_results/test/test_similarity/1/")
    parser.add_argument('--injection_path', default='./retrieval_results/test/related_tests_1.json')
    parser.add_argument("--proj", type=str, choices=["astropy", "django", "matplotlib", "seaborn", "flask", "requests", "xarray", "pylint", "pytest", "scikit-learn", "sphinx", "sympy"], default=None)
    parser.add_argument("--topk", type=int, default=5)
    parser.add_argument("--wo_call", action='store_true', default=False)
    parser.add_argument("--tree_path", type=str, default="./retrieval_results/swe_test_cgs/")
    parser.add_argument("--keywords_path", type=str, default="./retrieval_results/code/keywords_gpt-4o.json")
    parser.add_argument("--from_id", type=str, default=None)
    parser.add_argument("--swt", action='store_true', default=False)
    parser.add_argument("--tdd", action='store_true', default=False)
    parser.add_argument("--p1", type=float, default=0.1)
    args = parser.parse_args()

    gen_test_dir = args.gen_test_dir
    output_dir = args.output_dir
    injection_path = args.injection_path
    filter_proj = args.proj
    with open('swt.txt', 'r') as f:
        swt = f.read().strip().split('\n')
    with open('tdd.txt', 'r') as f:
        tdd = f.read().strip().split('\n')

    if args.tdd:
        swe_bench = load_dataset("princeton-nlp/SWE-bench_Verified")["test"]
        swe_bench = [bug_report for bug_report in swe_bench if bug_report["instance_id"] in tdd]
    elif args.swt:
        swe_bench = load_dataset("SWE-bench/SWE-bench_Lite")["test"]
        swe_bench = [bug_report for bug_report in swe_bench if bug_report["instance_id"] in swt]
    flag = False
    results = []
    for bug_report in swe_bench:
        bug_id = bug_report["instance_id"]
        proj = bug_report['repo']
        if filter_proj and proj.split('/')[-1] != filter_proj:
            continue
        
        if args.from_id and bug_id == args.from_id:
            flag = True
        if args.from_id and not flag:
            continue
        
        if os.path.exists(f"{output_dir}/{bug_id}") and os.path.exists(f"{output_dir}/{bug_id}/semantic.csv") and (args.wo_call or (os.path.exists(f"{output_dir}/{bug_id}/score.csv") and os.path.exists(f"{output_dir}/{bug_id}/call.csv"))):
            continue
        gen_test_path = f"{gen_test_dir}/{bug_id}_n1.txt"
        score_topk, semantic_topk, call_topk = retrieval(bug_report, gen_test_path, injection_path, args.topk, args.wo_call, args.p1, tree_path=args.tree_path, keywords_path=args.keywords_path)

        if not os.path.exists(f"{output_dir}/{bug_id}"):
            Path(f"{output_dir}/{bug_id}").mkdir(parents=True, exist_ok=True)
        semantic_topk.to_csv(f"{output_dir}/{bug_id}/semantic.csv", index=False)
        if not args.wo_call and score_topk is not None and call_topk is not None:
            score_topk.to_csv(f"{output_dir}/{bug_id}/score.csv", index=False)
            call_topk.to_csv(f"{output_dir}/{bug_id}/call.csv", index=False)
