import argparse
import multiprocessing
import os
import pickle
import shutil

import jedi
from scripts.config import REPO_ROOT_DIR, ROOT_DIR
from scripts.utils import swe_util
from scripts.utils.git_utils import initialize

from .search_utils import get_all_py_files, get_all_classes_in_file, get_class_signature, get_all_funcs_in_class_in_file
from .search_utils import get_func_snippet_in_class, get_top_level_functions, get_code_snippets, get_global_variables_corrected
from .search_utils import get_class_content
from enum import Enum
from tqdm import tqdm
from datasets import load_dataset
import subprocess as sp

class NodeType(Enum):
    _repo = "repo"
    _file = "file"
    _class = "class"
    _class_function = "class_function"
    _function = "top-level function"  # Regular function within the file
    _global_var = "global_var"

class Node:
    def __init__(self, obj_name: str, node_type: str, path: str,
                 code_start_line: int = -1, code_end_line: int = -1, code_content: str = ""):
        self.obj_name = obj_name
        self.node_type = node_type
        self.path = path
        self.code_start_line = code_start_line
        self.code_end_line = code_end_line
        self.code_content = code_content
        self.reference_who = []
        self.who_reference_me = []
        self.child = []
        self.parent = None
        # self.score = 0

    def __repr__(self):
        return f"{self.obj_name}: {self.node_type}"
    
    def get_full_name(self):
        if self.parent.obj_name == "repo_root":
            return self.path.replace(".py", "").replace("/", ".")
        return f"{self.parent.get_full_name()}.{self.obj_name}"
    
    def to_json(self):
        return {
            "obj_name": self.obj_name,
            "node_type": self.node_type,
            "path": self.path,
            "code_start_line": self.code_start_line,
            "code_end_line": self.code_end_line,
            "code_content": self.code_content,
            # "reference_who": [node.obj_name for node in self.reference_who],
            # "who_reference_me": [node.obj_name for node in self.who_reference_me],
            "parent": self.parent.obj_name if self.parent else None
        }

    def add_reference_me(self, child_node):
        self.who_reference_me.append(child_node)

    def add_reference_who(self, parent_node):
        self.reference_who.append(parent_node)

    def add_child(self, structure_child_node):
        self.child.append(structure_child_node)

    def set_parent(self, parent_node):
        self.parent = parent_node

    def print_child_info(self, level=0):
        if level > 3:
            return
        indent = "    " * level
        print(f"{indent}{self.obj_name}: {self.node_type}")
        if self.reference_who:
            print(f"{indent}- reference_who:{self.reference_who}")
        if self.who_reference_me:
            print(f"{indent}- who_reference_me:{self.who_reference_me}")
        for child in self.child:
            child.print_child_info(level + 1)

    def find_node_by_name(self, name):
        node_list = []
        for child in self.child:
            if child.obj_name == name:
                node_list.append(child)
            else:
                node_list.extend(child.find_node_by_name(name))
        return node_list

    def find_node_by_name_and_file(self, name, file_path):
        node_list = []
        # todo
        for child in self.child:
            if file_path in child.path and child.obj_name == name:
                node_list.append(child)
            else:
                node_list.extend(child.find_node_by_name_and_file(name, file_path))
        return node_list

    def find_all_node_by_file(self, file_path):
        node_list = []
        for child in self.child:
            if file_path == child.path and child.node_type != NodeType._file.value:
                node_list.append(child)

            node_list.extend(child.find_all_node_by_file(file_path))
        return node_list
    
    def find_node_by_module_path(self, proj, env_name, modules):
        node_list = self.find_node_by_name(modules[-1])
        if len(node_list) <= 1:
            return node_list
        filter_node = []
        proj = proj.split("/")[-1]
        project = jedi.Project(path=swe_util.repo_path(proj), environment_path=f"~/miniconda3/envs/{env_name}")
        if modules[0] == proj:
            modules = modules[1:]
        code  = f"from {proj} import {modules[0]}\n{'.'.join(modules)}()"
        jedi_script = jedi.Script(code=code, project=project)
        try:
            definitions = jedi_script.goto()
        except Exception as e:
            definitions = []
        if len(definitions) > 0:
            for node in node_list:
                for d in definitions:
                    if d.module_path == None:
                        print(f"Error: {proj}, {env_name}")
                        continue
                    path = os.path.relpath(d.module_path, swe_util.repo_path(proj))

                    if path == node.path:
                        node_full_name = node.get_full_name()
                        if d.full_name == node_full_name:
                            filter_node.append(node)
        else:
            for node in node_list:
                current_node = node
                index = len(modules) - 1
                while current_node.parent and index >= 0:
                    if current_node.obj_name != modules[index]:
                        break
                    current_node = current_node.parent
                    index -= 1
                if index == -1:
                    filter_node.append(node)
            
        return filter_node

def find_all_referencer(
    variable_name, file_path, line_number, column_number, repo_path, in_file_only=False
):
    project = jedi.Project(path=repo_path)
    script = jedi.Script(path=file_path, project=project)
    try:
        if in_file_only:
            references = script.get_references(
                line=line_number, column=column_number, scope="file"
            )
        else:
            references = script.get_references(line=line_number, column=column_number)
        variable_references = [ref for ref in references if ref.name == variable_name]
        # print('2.3. variable_references', len(variable_references))
        return [
            (os.path.relpath(ref.module_path, repo_path), ref.line, ref.column)
            for ref in variable_references
            if not (ref.line == line_number and ref.column == column_number)
        ]
    except Exception as e:
        print(f"Error occurred: {e}")
        return []

def get_graph_info(repo_path: str, id) -> Node:
    dg = Node(obj_name="repo_root", node_type=NodeType._repo.value, path=repo_path)

    all_py_files = get_all_py_files(repo_path)
    for file_path in tqdm(all_py_files, total=len(all_py_files), desc=id, leave=False):
        try:
            rel_path = os.path.relpath(file_path, repo_path)
            file_name = os.path.basename(file_path)
            file_name_without_ext = os.path.splitext(file_name)[0]

            file_node = Node(obj_name=file_name_without_ext, node_type=NodeType._file.value, path=rel_path,
                            code_start_line=-1, code_end_line=-1, code_content="file_content")

            all_class = get_all_classes_in_file(file_path)
            if all_class:
                for class_name, class_start_line, class_end_line in all_class:
                    class_content = get_class_signature(file_path, class_name)
                    class_node = Node(obj_name=class_name, node_type=NodeType._class.value, path=rel_path,
                                    code_start_line=class_start_line, code_end_line=class_end_line, code_content=class_content)
                    class_node.set_parent(file_node)
                    file_node.add_child(class_node)
                    all_funcs_in_class = get_all_funcs_in_class_in_file(file_path, class_name)
                    if all_funcs_in_class:
                        for func_name, func_start_line, func_end_line in all_funcs_in_class:
                            func_content = get_func_snippet_in_class(file_path, class_name, func_name)
                            func_node = Node(obj_name=func_name, node_type=NodeType._class_function.value, path=rel_path,
                                            code_start_line=func_start_line, code_end_line=func_end_line, code_content=func_content)
                            func_node.set_parent(class_node)
                            class_node.add_child(func_node)

            all_funcs = get_top_level_functions(file_path)
            if all_funcs:
                for func_name, func_start_line, func_end_line in all_funcs:
                    func_content = get_code_snippets(file_path, func_start_line, func_end_line)
                    func_node = Node(obj_name=func_name, node_type=NodeType._function.value, path=rel_path,
                                    code_start_line=func_start_line, code_end_line=func_end_line, code_content=func_content)
                    func_node.set_parent(file_node)
                    file_node.add_child(func_node)

            all_vars = get_global_variables_corrected(file_path)
            if all_vars:
                for var_name, var_start_line, var_end_line in all_vars:
                    var_content = get_code_snippets(file_path, var_start_line, var_end_line)
                    var_node = Node(obj_name=var_name, node_type=NodeType._global_var.value, path=rel_path,
                                    code_start_line=var_start_line, code_end_line=var_end_line, code_content=var_content)
                    var_node.set_parent(file_node)
                    file_node.add_child(var_node)

            file_node.set_parent(dg)
            dg.add_child(file_node)
        except Exception as e:
            print(f"Error processing file {file_path}: {e}")
            continue
    return dg

def get_graph_info_filter(repo_path: str, filter_path_list) -> Node:
    dg = Node(obj_name="repo_root", node_type=NodeType._repo.value, path=repo_path)
    all_py_files = filter_path_list
    for file_path in tqdm(all_py_files, total=len(all_py_files), desc='Stage 1', leave=False):
        with open(file_path, 'r') as f:
            code_content = f.read()
        rel_path = os.path.relpath(file_path, repo_path)
        file_name = os.path.basename(file_path)
        file_name_without_ext = os.path.splitext(file_name)[0]

        file_node = Node(obj_name=file_name_without_ext, node_type=NodeType._file.value, path=rel_path,
                         code_start_line=-1, code_end_line=-1, code_content=code_content)

        all_class = get_all_classes_in_file(file_path)
        if all_class:
            for class_name, class_start_line, class_end_line in all_class:
                class_content = get_class_content(file_path, class_start_line, class_end_line)
                class_node = Node(obj_name=class_name, node_type=NodeType._class.value, path=rel_path,
                                  code_start_line=class_start_line, code_end_line=class_end_line, code_content=class_content)
                class_node.set_parent(file_node)
                file_node.add_child(class_node)
                all_funcs_in_class = get_all_funcs_in_class_in_file(file_path, class_name)
                if all_funcs_in_class:
                    for func_name, func_start_line, func_end_line in all_funcs_in_class:
                        func_content = get_func_snippet_in_class(file_path, class_name, func_name)
                        func_node = Node(obj_name=func_name, node_type=NodeType._class_function.value, path=rel_path,
                                         code_start_line=func_start_line, code_end_line=func_end_line, code_content=func_content)
                        func_node.set_parent(class_node)
                        class_node.add_child(func_node)
        all_funcs = get_top_level_functions(file_path)
        if all_funcs:
            for func_name, func_start_line, func_end_line in all_funcs:
                func_content = get_code_snippets(file_path, func_start_line, func_end_line)
                func_node = Node(obj_name=func_name, node_type=NodeType._function.value, path=rel_path,
                                 code_start_line=func_start_line, code_end_line=func_end_line, code_content=func_content)
                func_node.set_parent(file_node)
                file_node.add_child(func_node)
        all_vars = get_global_variables_corrected(file_path)
        if all_vars:
            for var_name, var_start_line, var_end_line in all_vars:
                var_content = get_code_snippets(file_path, var_start_line, var_end_line)
                var_node = Node(obj_name=var_name, node_type=NodeType._global_var.value, path=rel_path,
                                code_start_line=var_start_line, code_end_line=var_end_line, code_content=var_content)
                var_node.set_parent(file_node)
                file_node.add_child(var_node)
        file_node.set_parent(dg)
        dg.add_child(file_node)

    for file_path in tqdm(all_py_files, total=len(all_py_files), desc='Stage 2', leave=False):

        rel_path = os.path.relpath(file_path, repo_path)
        project = jedi.Project(repo_path, load_unsafe_extensions=False)
        script = jedi.Script(path=file_path, project=project)
        all_names = script.get_names(all_scopes=True)
        all_node = dg.find_all_node_by_file(rel_path)
        for name in all_names:
            try:
                if name.type == 'statement' or name.type == 'param':
                    continue
            except Exception as e:
                print(f'[Error]: {e}')
                continue
            for temp_node in all_node:
                if temp_node.obj_name == name.name and temp_node.code_start_line == name.line:
                    all_reference = find_all_referencer(name.name, file_path, name.line, name.column, repo_path)
                    for reference in all_reference:
                        all_ref_file_node = dg.find_all_node_by_file(reference[0])
                        for file_node in all_ref_file_node:
                            if file_node.code_start_line <= reference[1] and file_node.code_end_line >= reference[1]:
                                if file_node in temp_node.child:
                                    continue
                                if file_node not in temp_node.who_reference_me:
                                    temp_node.add_reference_me(file_node)
                                if temp_node not in file_node.reference_who:
                                    file_node.add_reference_who(temp_node)
    dg.print_child_info()
    return dg


def save_graph(graph, file_path):
    """
    Save graph to file

    :param graph: The graph object to save.
    :param file_path: The file path to save the graph.
    """
    with open(file_path, 'wb') as file:
        pickle.dump(graph, file)

def load_graph(file_path):
    """
    Load graph from file.

    :param file_path: The path of the graph file.
    :return: The loaded graph object.
    """
    with open(file_path, 'rb') as file:
        graph = pickle.load(file)
    return graph

def initialize_repo(repo_path: str, commit_hash: str):
    """
    Switch to a specific commit at the designated repository path.
    This is a more general version of initialize.
    """
    print(f"[{os.getpid()}] Initializing repo at {repo_path} to commit {commit_hash[:7]}...")
    try:
        # Use subprocess to execute git commands in the specified directory
        sp.run(['git', 'checkout', commit_hash, '-f'], cwd=repo_path, check=True, capture_output=True, text=True)
        print(f"[{os.getpid()}] Git checkout successful.")
    except sp.CalledProcessError as e:
        print(f"[{os.getpid()}] !! Git checkout FAILED in {repo_path} !!")
        print(f"[{os.getpid()}] Stderr: {e.stderr}")
        raise e

def process_single_item(bug_report):
    """
    This is the function executed by parallel workers.
    Integrates the path processing logic provided and the original graph generation logic.
    """
    instance_id = bug_report["instance_id"]
    repo_name = bug_report['repo'].split('/')[-1]
    commit_hash = bug_report['base_commit']
    
    # 1. Define paths
    source_repo_path = os.path.join(REPO_ROOT_DIR, repo_name)
    # Create independent working directories for each instance_id
    workspace_repo_path = os.path.join(REPO_ROOT_DIR, instance_id, repo_name)
    
    pid = os.getpid()

    try:
        # 2. Prepare workspace (isolated environment)
        if not os.path.exists(workspace_repo_path):
            # print(f"[{pid}] Copying workspace for {instance_id}...")
            os.makedirs(os.path.dirname(workspace_repo_path), exist_ok=True)
            # Use copytree to copy the repository
            shutil.copytree(source_repo_path, workspace_repo_path, dirs_exist_ok=True)
        
        # 3. Initialize repository state
        initialize_repo(workspace_repo_path, commit_hash)
        
        # 4. Execute core graph generation logic
        # Note: Here we pass the isolated workspace_repo_path
        dg = get_graph_info(workspace_repo_path, instance_id)
        
        print(f"[{pid}] Graph generation completed for {instance_id}.")
        # 5. Save results
        output_dir = GRAPH_PATH
        os.makedirs(output_dir, exist_ok=True)
        save_path = os.path.join(output_dir, f'{instance_id}_graph.pkl')
        
        # Assume save_graph is originally defined by you or a function in swe_util
        # swe_util.save_graph(dg, save_path) 
        with open(save_path, 'wb') as f:
            pickle.dump(dg, f)
        
        # Once completed, delete the workspace to save space
        shutil.rmtree(os.path.join(REPO_ROOT_DIR, instance_id))
        return f"Success: {instance_id}"

    except Exception as e:
        return f"Error processing {instance_id}: {str(e)}"


MAX_WORKERS = 16
if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument("--graph_path", type=str, default="./retrieval_results/graphs", help="Path to save the repo graph.")
    parser.add_argument("--swt", action="store_true", default=False, help="Whether to use the SWT-bench dataset.")
    parser.add_argument("--tdd", action="store_true", default=False, help="Whether to use the TDD-bench dataset.")
    args = parser.parse_args()
    graph_path = args.graph_path
    use_swt = args.swt
    use_tdd = args.tdd
    # 1. Prepare data
    print("Loading dataset...")
    with open("swt.txt", "r") as f:
        swt = f.read().strip().split("\n")
    with open("tdd.txt", "r") as f:
        tdd = f.read().strip().split("\n")
    if use_swt:
        ds = load_dataset("SWE-bench/SWE-bench_Lite")["test"]
    elif use_tdd:
        ds = load_dataset("princeton-nlp/SWE-bench_Verified")["test"]
    else:
        raise NotImplementedError()

    # 2. Pre-filtering tasks
    tasks = []
    flag = False
    
    print("Filtering tasks...")
    for bug_report in ds:
        bug_id = bug_report["instance_id"]
        
        if os.path.exists(os.path.join(graph_path, f"{bug_id}_graph.pkl")):
            continue
            
        tasks.append(bug_report)

    print(f"Total tasks to process: {len(tasks)}")

    # 3. Parallel execution
    # Use imap_unordered to display real-time progress with tqdm
    with multiprocessing.Pool(processes=MAX_WORKERS) as pool:
        results = list(tqdm(
            pool.imap_unordered(process_single_item, tasks), 
            total=len(tasks),
            desc="Processing Graphs",
            leave=False
        ))

    # 4. (Optional) Print error logs
    for res in results:
        if res.startswith("Error"):
            print(res)
