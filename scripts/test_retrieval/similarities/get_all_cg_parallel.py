import argparse
from collections import defaultdict
import glob
import json
import os
import shutil
import subprocess
from datasets import load_dataset
import sqlite3
import concurrent.futures

from tqdm import tqdm

from pyan.analyzer import CallGraphVisitor
from pyan.node import Node, Flavor
from scripts.config import REPO_ROOT_DIR
from scripts.utils.git_utils import initialize
from scripts.utils.swe_util import repo_path, swe_test_path_prefix

MAX_CALL_DEPTH = 5
MAX_WORKERS = 16

def get_module_name(filename, root):
    """
    Get the Python module name from a filename.
    E.g., /path/to/root/my/module.py -> my.module
    """
    if root:
        filename = os.path.relpath(filename, start=root)
    name = os.path.splitext(filename)[0]
    return name.replace(os.path.sep, '.')

def get_process_id(max_workers):
    """
    Get the unique ID of the current process in the process pool (0 to max_workers-1).
    Note: This depends on internal implementation details but is generally effective.
    """
    # _identity is an internal tuple of the process object, and the first element is its sequence number
    process_identity = os.getpid() # Fallback
    try:
        # For Python 3.8+ ProcessPoolExecutor
        from multiprocessing.process import BaseProcess
        process_identity = BaseProcess.current_process()._identity[0] - 1
    except (AttributeError, IndexError):
        # Fallback for other versions or executors
        pass
    
    # Ensure ID is within range
    return process_identity % max_workers

def list_py_files(directory: str, is_test: bool = False) -> list[str]:
    """Recursively finds all .py files in a directory."""
    test_dir = directory + '**/*.py'
    test_files = glob.glob(test_dir, recursive=True)
    if is_test:
        test_files = [test_file for test_file in test_files if test_file.split('/')[-1].startswith('test') or test_file.split('/')[-1].startswith('unittest')]
    return test_files

def extract_tree_from_graph(start_node: Node, visitor: CallGraphVisitor, visited: set, max_depth: int, current_depth: int = 0) -> dict:
    """
    Recursively extracts a call tree from the global graph with a depth limit.
    """
    # Base Case 1: Stop if the maximum depth is reached.
    if current_depth >= max_depth:
        return {'name': start_node.get_name(), 'children': [], 'truncated': True}

    # Base Case 2: Stop if we detect a cycle (recursion).
    if start_node in visited:
        return {'name': start_node.get_name(), 'children': [], 'recursive': True}

    visited.add(start_node)
    
    children = []
    # Get and sort all functions called by the current node.
    callees = sorted(list(visitor.uses_edges.get(start_node, set())), key=lambda node: node.get_name())
    
    for child_node in callees:
        # Recursive Step: Increment the current_depth for the next level.
        child_tree = extract_tree_from_graph(
            child_node, 
            visitor, 
            visited.copy(), 
            max_depth, 
            current_depth + 1
        )
        children.append(child_tree)

    return {'name': start_node.get_name(), 'children': children}

def get_functions_in_tree(tree: dict) -> set[str]:
    """Flattens a tree to get a unique set of all function names."""
    functions = {tree['name']}
    for child in tree['children']:
        functions.update(get_functions_in_tree(child))
    return functions

def build_all_cg(repo_dir: str, test_dir: str, output_dir: str, pid: int):
    """
    Main function to analyze a repo, build call trees for each test, and create a DF map.
    """
    print(f"1. Setting up output directory: {output_dir}")
    os.makedirs(output_dir, exist_ok=True)
    # Use sqlite to store call trees
    db_path = os.path.join(output_dir, "call_trees.db")
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute('''
CREATE TABLE IF NOT EXISTS call_trees (
    test_name TEXT PRIMARY KEY,
    tree_json TEXT NOT NULL
)
''')
    conn.commit()


    # 1. Find all Python files in the repository for a complete analysis
    print(f"2. Discovering all Python files in: {repo_dir}")
    all_files_in_testsuite = list_py_files(test_dir)
    test_files = list_py_files(test_dir, is_test=True)
    print(f" Found {len(all_files_in_testsuite)} files in testsuite and {len(test_files)} test files.")

    # 2. Perform static analysis on the ENTIRE repository once
    print("\n3. Running static analysis on the entire repository... (This may take a moment)")
    visitor = CallGraphVisitor(test_files, root=repo_dir, pid=pid)
    print("   Analysis complete. Call graph constructed.")

    # 3. For each test file, identify test functions and build their call trees
    print("\n4. Building call trees for each test function...")
    all_test_trees = []
    
    # Find all function nodes that are defined in our test files
    test_function_nodes = []
    for nodes_list in visitor.nodes.values():
        for node in nodes_list:
            assert isinstance(node, Node)
            if node.filename in test_files and (node.flavor == Flavor.FUNCTION or node.flavor == Flavor.METHOD) and node.get_short_name().startswith('test'):
                test_function_nodes.append(node)
    
    print(f"   Found {len(test_function_nodes)} test functions to process.")

    for test_node in tqdm(test_function_nodes, position=pid, leave=False):
        # print(f"   - Building tree for: {test_node.get_name()}")
        call_tree = extract_tree_from_graph(
            start_node=test_node,
            visitor=visitor,
            visited=set(),
            max_depth=MAX_CALL_DEPTH 
        )
        all_test_trees.append(call_tree)
        
        test_name = test_node.get_name()
        tree_json_str = json.dumps(call_tree)
        
        # Insert or replace data
        cursor.execute(
            "INSERT OR REPLACE INTO call_trees (test_name, tree_json) VALUES (?, ?)", 
            (test_name, tree_json_str)
        )

    conn.commit()
    conn.close()

    # 4. Build the Document Frequency (DF) map from all generated trees
    print("\n5. Building Document Frequency (DF) map...")
    df_map = build_df(all_test_trees, len(all_test_trees))
    
    df_output_path = os.path.join(output_dir, "df.json")
    print(f"   Saving DF map to: {df_output_path}")
    with open(df_output_path, 'w', encoding='utf-8') as f:
        json.dump(df_map, f)
        
    print("\n✅ All tasks completed successfully!")

def build_df(all_trees: list[dict], total_docs: int) -> dict:
    """Builds the document frequency map from a list of call trees."""
    df_counts = defaultdict(int)
    for tree in all_trees:
        # Get unique function names for this document (tree)
        unique_functions = get_functions_in_tree(tree)
        unique_functions.remove(tree['name'])  # Exclude the root function itself
        for func_name in unique_functions:
            df_counts[func_name] += 1
            
    return {
        "total_documents": total_docs,
        "df": dict(df_counts)
    }

def initialize_repo(repo_path: str, commit_hash: str):
    """
    Switch to a specific commit at the designated repository path.
    This is a more general version of initialize.
    """
    print(f"[{os.getpid()}] Initializing repo at {repo_path} to commit {commit_hash[:7]}...")
    try:
        # Use subprocess to execute git commands in the specified directory
        subprocess.run(['git', 'checkout', commit_hash, '-f'], cwd=repo_path, check=True, capture_output=True, text=True)
        print(f"[{os.getpid()}] Git checkout successful.")
    except subprocess.CalledProcessError as e:
        print(f"[{os.getpid()}] !! Git checkout FAILED in {repo_path} !!")
        print(f"[{os.getpid()}] Stderr: {e.stderr}")
        raise e

def process_bug_report_in_workspace(bug_report):
    """
    This is the core worker function for child processes.
    It is responsible for creating a workspace for a bug report, preparing the environment, and executing analysis.
    """
    instance_id = bug_report["instance_id"]
    repo_name = bug_report['repo'].split('/')[-1]
    commit_hash = bug_report['base_commit']
    
    # 1. Define source and target paths
    source_repo_path = os.path.join(REPO_ROOT_DIR, repo_name)
    workspace_repo_path = os.path.join(REPO_ROOT_DIR, instance_id, repo_name)

    process_id = get_process_id(MAX_WORKERS)
    
    try:
        # 2. Prepare workspace (if it doesn't exist, copy from source directory)
        if not os.path.exists(workspace_repo_path):
            print(f"[{os.getpid()}] Workspace for {instance_id} not found. Copying from {source_repo_path}...")
            os.makedirs(os.path.dirname(workspace_repo_path), exist_ok=True)
            shutil.copytree(source_repo_path, workspace_repo_path)
            print(f"[{os.getpid()}] Workspace for {instance_id} created at {workspace_repo_path}")
        else:
            print(f"[{os.getpid()}] Workspace for {instance_id} already exists. Reusing.")

        # 3. Initialize repository state in the isolated workspace
        initialize_repo(workspace_repo_path, commit_hash)

        # 4. Execute analysis tasks in the isolated workspace
        print(f"[{os.getpid()}] Starting analysis for {instance_id}...")
        test_dir = os.path.join(workspace_repo_path, swe_test_path_prefix(bug_report['repo'], instance_id))
        output_dir = os.path.join(OUTPUT_ROOT_DIR, instance_id)
        
        # Call your original core business logic function
        # Ensure build_all_cg uses the path within the isolation zone
        build_all_cg(
            repo_dir=workspace_repo_path, 
            test_dir=test_dir, 
            output_dir=output_dir,
            pid=process_id
        )
        
        print(f"✅ [{os.getpid()}] Successfully processed {instance_id}.")
        return f"Success: {instance_id}"

    except Exception as e:
        error_message = f"❌ [{os.getpid()}] FAILED to process {instance_id}: {e}"
        print(error_message)
        # Return exception information so the main process knows which task failed
        return error_message


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Parallel building of project call graphs (using workspace isolation strategy).")
    parser.add_argument("--output_dir", type=str, default='./retrieval_results/swe_test_cgs/')
    parser.add_argument("--proj", type=str, default="django")
    parser.add_argument("--max_workers", type=int, default=MAX_WORKERS, help="Concurrent processes count")
    parser.add_argument("--swt", action='store_true', default=False)
    parser.add_argument("--tdd", action='store_true', default=False)
    args = parser.parse_args()

    OUTPUT_ROOT_DIR = args.output_dir

    with open('swt.txt', 'r') as f:
        swt = f.read().strip().split('\n')
    with open('tdd.txt', 'r') as f:
        tdd = f.read().strip().split('\n')
    if args.tdd:
        swe_bench = load_dataset("princeton-nlp/SWE-bench_Verified")["test"]
        swe_bench = [bug_report for bug_report in swe_bench if bug_report["instance_id"] in tdd]
    else:
        swe_bench = load_dataset("SWE-bench/SWE-bench_Lite")["test"]
        swe_bench = [bug_report for bug_report in swe_bench if bug_report["instance_id"] in swt]
    
    tasks = []
    flag = False
    for bug_report in swe_bench:
        if args.proj and bug_report['repo'].split('/')[-1] != args.proj:
            continue
        if os.path.exists(os.path.join(OUTPUT_ROOT_DIR, bug_report["instance_id"], "call_trees.db")) and os.path.exists(os.path.join(OUTPUT_ROOT_DIR, bug_report["instance_id"], "df.json")):
            continue
        
        bug_id = bug_report["instance_id"]
        tasks.append(bug_report)

    if not tasks:
        print(f"No tasks found for project '{args.proj}'.")
        exit()

    print(f"Preparing to process {len(tasks)} tasks using up to {args.max_workers} parallel processes.")
    print(f"Template repository root directory: {REPO_ROOT_DIR}")
    print(f"Analysis results root directory: {OUTPUT_ROOT_DIR}")
    
    # Execute tasks using ProcessPoolExecutor
    with concurrent.futures.ProcessPoolExecutor(max_workers=args.max_workers) as executor:
        # Use executor.map to maintain the original task order and handle task distribution automatically
        # tqdm can wrap map to display a progress bar effectively
        results = list(tqdm(executor.map(process_bug_report_in_workspace, tasks), total=len(tasks)))

    print(f"\n" * (MAX_WORKERS + 1))
    print("\n--- All tasks completed ---")
    success_count = 0
    failures = []
    for r in results:
        if r.startswith("Success"):
            success_count += 1
        else:
            failures.append(r)
    
    print(f"Total success: {success_count}/{len(tasks)}")
    if failures:
        print("\nFailed task details:")
        for f in failures:
            print(f" - {f}")
