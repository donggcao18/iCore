import argparse
import os
import subprocess as sp
from datasets import load_dataset
import json
from tqdm import tqdm

# from scripts.swe_util import repo_path
from scripts.code_retrieval.repo_graph.graph import load_graph, Node
from scripts.utils import swe_util
from scripts.utils.git_utils import initialize

def find_node_by_file_path(path_list):
    node_list = []
    return []

def retrieve_keywords(dg:Node, keywords, bug_report):
    results = {}
    for keyword in keywords:
        # parse keyword
        if keyword.endswith('.py'):
            # file
            # TODO: Skipped for now
            nodes = []
            # if '/' in keyword:
            #     # path/to/file.py
            #     nodes = find_node_by_file_path(keyword.split('/'))
            #     if len(nodes) == 0:
            #         # print(f"Wrong path {keyword}")
            #         nodes = dg.find_node_by_name(keyword.split('/')[-1])
            #         # if len(nodes) == None:
            #             # print(f"File not found! {keyword.split('/')[-1]}")
            #             # break
            #     # results[keyword] = nodes
            # else:
            #     # file.py
            #     nodes = dg.find_node_by_name(keyword)
                # if len(nodes) == 0:
                    # print(f"File not found! {keyword}")
                    # break
                # results[keyword] = nodes
        elif '.' in keyword:
            # module.module.Class.function
            modules = keyword.split('.')
            proj = bug_report["repo"]
            env_name = swe_util.get_env_name(bug_report)
            nodes = dg.find_node_by_module_path(proj, env_name, modules)
            if len(nodes) == 0:
                # print(f"Wrong path {keyword}")
                nodes = dg.find_node_by_name(modules[-1])
                # if len(nodes) == 0:
                    # print(f"Node not found! {modules[-1]}")
                #     break
            # results[keyword] = nodes
        elif '.' not in keyword:
            # Class/function
            nodes = dg.find_node_by_name(keyword)
            # Need to sort nodes/score them
            # if len(nodes) == 0:
                # print(f"Node not found! {keyword}")
            # results[keyword] = nodes
            
        # nodes = [node.to_json() for node in nodes]
        results[keyword] = nodes
            
    return results

def filter_retrieval_results(results):
    node_score = {}
    file_score = {}
    for keyword, nodes in results.items():
        if len(nodes) == 0:
            continue
        basic_score = 1/len(nodes)
        for node in nodes:
            path = node.path
            file_score[path] = file_score.get(path, 0) + basic_score
            node_score[node] = node_score.get(node, 0) + basic_score
    
    for keyword, nodes in results.items():
        final_node_score = {}
        for node in nodes:
            final_node_score[node] = node_score[node] + file_score[node.path] + node_score.get(node.parent, 0)
        sorted_nodes = sorted(final_node_score.items(), key=lambda x: x[1], reverse=True)
        # results[keyword] = [node[0].to_json() for node in sorted_nodes]
        # Keep only the first one
        results[keyword] = sorted_nodes[0][0].to_json() if len(sorted_nodes) > 0 else None
                
    return results

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument("--keywords_path", type=str, default="./retrieval_results/code/keywords.json", help="Path to save the extracted keywords.")
    parser.add_argument("--graph_dir", type=str, default="./retrieval_results/graphs", help="Directory to save the graph files.")
    parser.add_argument("--save_path", type=str, default="./retrieval_results/code/retrieval_results.json", help="Path to save the retrieval results.")
    parser.add_argument("--swt", action="store_true", help="Whether to use the SWT-bench dataset.")
    parser.add_argument("--tdd", action="store_true", help="Whether to use the TDD-bench dataset.")
    args = parser.parse_args()
    keywords_path = args.keywords_path
    graph_dir = args.graph_dir
    use_swt = args.swt
    use_tdd = args.tdd
    if use_swt:
        ds = load_dataset("SWE-bench/SWE-bench_Lite")["test"]
        with open("swt.txt", "r") as f:
            swt = f.read().strip().split("\n")
        ds = [bug_report for bug_report in ds if bug_report["instance_id"] in swt]
    elif use_tdd:
        ds = load_dataset("SWE-bench/SWE-bench_Verified")["test"]
        with open("tdd.txt", "r") as f:
            tdd = f.read().strip().split("\n")
        ds = [bug_report for bug_report in ds if bug_report["instance_id"] in tdd]
    
    retrieval_results = {}
    if os.path.exists(args.save_path):
        with open(args.save_path, "r") as f:
            retrieval_results = json.load(f)
    
    with open(keywords_path, "r") as f:
        all_keywords = json.load(f)
    

    for bug_report in tqdm(ds):
        id = bug_report["instance_id"]
        if use_swt and id not in swt:
            continue
        if use_tdd and id not in tdd:
            continue
        if id in retrieval_results:
            continue
        proj = bug_report["repo"].split("/")[-1]
        repo_path = swe_util.repo_path(proj)
        initialize(bug_report)

        graph_path = os.path.join(graph_dir, f"{id}_graph.pkl")
        dg = load_graph(graph_path)
        keywords = all_keywords[id]

        results = retrieve_keywords(dg, keywords, bug_report)
        filter_results = filter_retrieval_results(results)
        retrieval_results[id] = results
    with open(args.save_path, "w") as f:
        json.dump(retrieval_results, f, indent=4)