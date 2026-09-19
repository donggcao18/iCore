import os

from scripts.config import REPO_ROOT_DIR
from scripts.libro.postprocess_swe import inject_test
from scripts.utils.swe_util import get_env_name, repo_path

from scripts.test_retrieval.similarities.tree_edit_distance import calc_tree_distance, WeightedTreeBuilder
from scripts.test_retrieval.utils import Test, similarity_minmax_normalize


def load_first_test(bug_id):
    with open(f'data/first_query/gen_tests/{bug_id}_n1.txt') as f:
        return f.read()

def build_gen_test_call_graph(bug_report, gen_test_content, injection_path, tree_path, keywords_path='scripts/retrieval/keywords.json'):
    bug_id = bug_report["instance_id"]
    repo_abs_path = repo_path(bug_report['repo'])
    env_name = get_env_name(bug_report)
    tree_builder = WeightedTreeBuilder(bug_report['repo'], bug_id, tree_path, keywords_path=keywords_path)
    try:
        gen_test_names = inject_test(bug_report['repo'], gen_test_content, env_name, bug_id, pos=injection_path)
        print(gen_test_names)
    except Exception as e:
        return None, None
    gen_test_file = gen_test_names[0].split('::')[0]
    gen_test_name = gen_test_names[0].split('::')[1]
    gen_test_file_abs_path = os.path.join(repo_abs_path, gen_test_file)
    gen_test_module = gen_test_file_abs_path.replace(REPO_ROOT_DIR+'/', '').replace('.py', '').replace('/', '.') + '.' + gen_test_name
    
    gen_test_use_tree = tree_builder.get_call_graph([gen_test_file_abs_path], repo_abs_path, gen_test_module)
    
    return gen_test_use_tree, tree_builder

def get_call_graph_similarities(tree_builder: WeightedTreeBuilder, gen_test_use_tree, tests: list[Test], p1: float):
    similarities = []
    
    # First, set the weights for the query tree
    tree_builder.set_tree_weight(gen_test_use_tree, p1)
    gen_test_tree_weight = gen_test_use_tree.total_weight()

    for test in tests:
        # Construct the full test name (e.g., class.method or just function)
        if test.class_name:
            full_test_name = f"{test.class_name}.{test.test_name}"
        else:
            full_test_name = test.test_name
        
        # Call the new method with the file path and full test name
        repo_test_use_tree = tree_builder.get_call_graph_from_db(
            abs_file_path=test.abs_file_path,
            rel_file_path=test.rel_file_path,
            test_name=full_test_name
        )

        if not repo_test_use_tree:
            similarity = 0.0
        else:
            tree_builder.set_tree_weight(repo_test_use_tree)
            distance = calc_tree_distance(gen_test_use_tree, repo_test_use_tree)
            similarity = 1 - distance / (gen_test_tree_weight + repo_test_use_tree.total_weight())

        similarities.append(similarity)

    # It's good practice to close the DB connection when you're done
    tree_builder.close_connection()

    return similarities

def get_call_similarities(tree_builder, gen_test_use_tree, all_test_objs, p1):
    call_similarities = []
    # Calculate call graph similarity
    call_similarities = get_call_graph_similarities(tree_builder, gen_test_use_tree, all_test_objs, p1)
    
    # Normalize similarity
    call_similarities = similarity_minmax_normalize(call_similarities)
    # call_similarities = similarity_softmax_normalize(call_similarities)
    
    return call_similarities
