"""
Configuration for LLM test generator w/ different datasets.
"""
import os

REPO_ROOT_DIR = '/research/cbim/vast/qt60/any-ssr/utils/iCore/repos'
ROOT_DIR = '.'
ENV_NAME_TEMPLATE = 'setup_{name1}_{name2}__{version}'

API_KEY = {
    'gpt-4o': os.getenv('OPENAI_API_KEY'),
    'gpt-4o-2024-08-06': os.getenv('OPENAI_API_KEY'),
    'qwen-32b': os.getenv('QWEN_API_KEY'),
    'qwen/qwen3.8-27b:free': os.getenv('QWEN_API_KEY'),
    'nvidia/nemotron-3-super-120b-a12b:free': os.getenv('QWEN_API_KEY'),
    'z-ai/glm-5.2:free': os.getenv('QWEN_API_KEY'),
    'deepseek-v3-0324': os.getenv('DEEPSEEK_API_KEY'),
}

BASE_URL = {
    'gpt-4o': os.getenv('OPENAI_BASE_URL') or None,
    'gpt-4o-2024-08-06': os.getenv('OPENAI_BASE_URL') or None,
    'qwen-32b': os.getenv('QWEN_BASE_URL') or None,
    'qwen/qwen3.8-27b:free': os.getenv('QWEN_BASE_URL') or None,
    'nvidia/nemotron-3-super-120b-a12b:free': os.getenv('QWEN_BASE_URL') or None,
    'z-ai/glm-5.2:free': os.getenv('QWEN_BASE_URL') or None,
    'deepseek-v3-0324': os.getenv('DEEPSEEK_BASE_URL') or None,
}
