import json
import os

import numpy as np

from scripts.utils.swe_util import repo_path
from scripts.generator.make_prompt_util import get_function_content
from scripts.utils.swe_util import instance_id_to_proj

SEMANTIC_SIMILARITY_THRESHOLD = 0.5
FUNC_CALL_SIMILARITY_THRESHOLD = 0.5

class Test:
    def __init__(self, proj, rel_file_path, class_name, test_name, test_content):
        self.proj = proj
        self.rel_file_path = rel_file_path
        self.class_name = class_name
        self.test_name = test_name
        self.test_content = test_content
        self.name_similarity = 0
        self.bm25_similarity = 0
        self.semantic_similarity = 0
        self.func_call_similarity = 0
        self.llm_rerank_file_match = False
        self.llm_rerank_test_match = False
        self.score = 0
        self.abs_file_path = os.path.join(repo_path(proj), rel_file_path)
    
    def __str__(self):
        return f"{self.rel_file_path} {self.class_name} {self.test_name} {self.score}"
    
    def __repr__(self):
        return self.__str__()
    
    def to_json(self):
        return {
            "proj": self.proj,
            "file_path": self.rel_file_path,
            "class_name": self.class_name,
            "test_name": self.test_name,
            "name_similarity": self.name_similarity,
            "bm25_similarity": self.bm25_similarity,
            "semantic_similarity": self.semantic_similarity,
            "func_call_similarity": self.func_call_similarity,
            "llm_rerank_file_match": self.llm_rerank_file_match,
            "llm_rerank_test_match": self.llm_rerank_test_match,
            "score": self.score
        }
    
    def calc_final_score(self):
        # 1. name similarity
        # 2. call graph similarity
        # 3. function calling result
        # 4. function calling result
        self.score = self.semantic_similarity * SEMANTIC_SIMILARITY_THRESHOLD + \
                    self.func_call_similarity * FUNC_CALL_SIMILARITY_THRESHOLD
        
def get_function_calling_results(bug_id, related_test_path):
    with open(related_test_path, 'r') as f:
        results = json.load(f)
    return results[bug_id]

def similarity_minmax_normalize(similarities):
    """Normalize similarities to the [0, 1] range."""
    min_sim = min(similarities)
    max_sim = max(similarities)
    if min_sim == max_sim:
        return [0.0] * len(similarities)
    return [(sim - min_sim) / (max_sim - min_sim) for sim in similarities]
    
def similarity_softmax_normalize(similarities):
    """Normalize similarities to the [0, 1] range."""
    exp_sim = np.exp(similarities)
    return exp_sim / np.sum(exp_sim)

def check_not_in(related_tests, file, test_name):
    for test in related_tests:
        if test["file"] == file and test["name"].split('.')[-1] == test_name.split('.')[-1]:
            return False
    return True

def get_related_test(proj, test_files):
    related_tests = []
    for i, test in enumerate(test_files):
        file = test[0]
        test_name = test[1]
        function_content = get_function_content(proj, file, test_name)
        if len(function_content) > 0 and check_not_in(related_tests, file, test_name):
            related_tests.append({
                "name": test_name,
                "file": file,
                "code_content": function_content
            })
    
    return related_tests

def get_related_tests_for_instance(instance_id, result_path):
    with open(result_path, 'r') as f:
        results = json.load(f)
    test_files = results.get(instance_id, [])
    proj = instance_id_to_proj(instance_id)
    return get_related_test(proj, test_files)