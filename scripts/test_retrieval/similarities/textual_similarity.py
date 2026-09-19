import numpy as np
from scripts.test_retrieval.utils import Test, similarity_minmax_normalize
from scripts.utils.git_utils import *
import re
from nltk.stem import WordNetLemmatizer
from rank_bm25 import BM25Okapi

def get_gen_test_name(gen_test_content):
    names = []

    lines = gen_test_content.split('\n')
    for line in lines:
        if 'class ' in line and 'Test' in line:
            class_name = line.split('class ')[1].split('(')[0]
            names.append(class_name)
        if 'def test_' in line:
            function_name = line.split('def ')[1].split('(')[0]
            names.append(function_name)
    return names

def filter_test_cases(test_cases):
    # return test cases with score > threshold
    sorted_tests = sorted(test_cases, key=lambda x: x.score, reverse=True)
    if len(sorted_tests) >= 20:
        return sorted_tests[:20]
    else:
        return sorted_tests

def preprocess_str(s):
    s = normalize_and_tokenize(s)
    return set(s)

def normalize_and_tokenize(name: str) -> list[str]:
    """Normalization and tokenization: remove punctuation, convert to lowercase, split snake_case and CamelCase, and preserve abbreviations like SQL."""
    lemmatizer = WordNetLemmatizer()
    if ".py" in name:
        name = name.replace(".py", "")
    # Special handling for CamelCase and abbreviations: rawSQL → raw_SQL
    name = re.sub(r'(?<=[a-z0-9])(?=[A-Z])', '_', name)
    name = re.sub(r'(?<=[A-Z])(?=[A-Z][a-z])', '_', name)  # e.g., ABCd → AB_Cd
    name = name.lower()

    # Remove non-alphanumeric and non-underscore characters
    name = re.sub(r'[^a-z0-9_]', ' ', name)
    # Tokenization
    tokens = re.split(r'[_\s]+', name)
    
    ret = []
    for token in tokens:
        if not token:
            continue
        token = lemmatizer.lemmatize(token)
        if token == "test":
            continue
        ret.append(token)
    return ret

def jaccard_similarity(set1, set2):
    """Calculate Jaccard similarity."""
    intersection = len(set1.intersection(set2))
    union = len(set1.union(set2))
    return intersection / union if union != 0 else 0.0

def get_name_similarity(gen_test_content, existing_test_names: list[str]):
    gen_test_names = ' '.join(get_gen_test_name(gen_test_content))

    # Calculate BM25 similarity
    tokenized_names = [normalize_and_tokenize(name) for name in existing_test_names]
    bm25 = BM25Okapi(tokenized_names)
    bm25_scores = bm25.get_scores(normalize_and_tokenize(gen_test_names))
    similarities = similarity_minmax_normalize(bm25_scores)
    return similarities

def get_code_similarity(gen_test_content, existing_test_contents):
    tokenized_documents = [normalize_and_tokenize(doc) for doc in existing_test_contents]
    # Tokenize the query
    tokenized_query = normalize_and_tokenize(gen_test_content)

    bm25 = BM25Okapi(tokenized_documents)
    doc_scores = bm25.get_scores(tokenized_query)

    # Normalize BM25 scores
    doc_scores = similarity_minmax_normalize(doc_scores)
    return doc_scores


def get_semantic_similarity(gen_test_content, existing_tests: list[Test], weight={'name': 0.5, 'bm25': 0.5}):
    name_similarities = get_name_similarity(gen_test_content, [f"{t.rel_file_path} {t.class_name} {t.test_name}" for t in existing_tests])
    bm25_similarities = get_code_similarity(gen_test_content, [t.test_content for t in existing_tests])

    # Calculate comprehensive semantic score
    semantic_similarities = []
    for existing_test, name_similarity, bm25_similarity in zip(existing_tests, name_similarities, bm25_similarities):
        semantic_score = weight['name'] * name_similarity + weight['bm25'] * bm25_similarity
        existing_test.name_similarity = name_similarity
        existing_test.bm25_similarity = bm25_similarity
        semantic_similarities.append(semantic_score)

    # Normalize semantic similarity
    semantic_similarities = similarity_minmax_normalize(semantic_similarities)
    
    return semantic_similarities