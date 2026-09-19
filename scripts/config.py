"""
Configuration for LLM test generator w/ different datasets.
"""
REPO_ROOT_DIR = '/PATH/TO/swe_repos'
ROOT_DIR = '.'
ENV_NAME_TEMPLATE = 'setup_{name1}_{name2}__{version}'

API_KEY = {
    'gpt-4o': 'YOUR_API_KEY_FOR_GPT_4O',
    'gpt-4o-2024-08-06': 'YOUR_API_KEY_FOR_GPT_4O_2024_08_06',
    'qwen-32b': 'YOUR_API_KEY_FOR_QWEN_32B',
    'deepseek-v3-0324': 'YOUR_API_KEY_FOR_DEEPSEEK_V3_0324',
}

BASE_URL = {
    'gpt-4o': '',
    'gpt-4o-2024-08-06': '',
    'qwen-32b': '',
    'deepseek-v3-0324': '',
}
