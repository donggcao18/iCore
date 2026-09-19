import argparse
import os
from datasets import load_dataset
import json
import ast
import re

from tqdm import tqdm

from scripts.utils.llm_api import query_chat_llm
import logging

KEYWORD_EXTRACT_PROMPT = """\
You are a developer investigating a bug report. Your task is to extract critical code elements and relevant technical keywords from the report. These elements should be directly useful for reproducing the bug, or searching the codebase.

Guidelines:  
1. Identify and list only the code-related elements that are essential for searching the codebase and understanding or reproducing the bug. These may include function names, class names, method names, variable names, file names, or other identifiers that directly contribute to debugging.
2. Exclude non-actionable or irrelevant terms, including: 
- Generic words like `"feature"`, `"error"`, `"problem"`.
- User-defined class names or model names that are created within the example bug report but are unlikely to exist in the actual codebase (e.g., `A`, `B`, `C` in a sample model definition).
- Any abstract or non-code terms that do not directly contribute to debugging.
3. Preserve the exact names or formats of the elements as written in the bug report. If an imported element is renamed using as, restore its original module path. For example, if the bug report mentions `import pandas as pd`, and pd.DataFrame is used in the code, extract it as `pandas.DataFrame`.
4. Prioritize the extracted elements by their importance for reproducing the bug:
- Elements that are most likely to be useful or necessary for understanding the bug should be ranked highest.
- Additionally, class names and function/method names should be ranked higher than code fragments.

**Output format:** Provide the extracted code elements as a list in the following format:  
```python
['ClassName', 'ClassName.methodName', 'functionName', 'variableName']
```

For example, if the bug report mentions a class `MyClass` and methods `function1` and `function2` inside it, the output should be:  
```python
['MyClass', 'MyClass.function1', 'MyClass.function2']
```.

### Bug Report:
{bug_report}
"""

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def extract_keywords(bug_report, model):
    id = bug_report["instance_id"]

    log_file = f'./keywords_log/{model}/{id}.log'
    if not os.path.exists(f"./keywords_log/{model}"):
        os.makedirs(f"./keywords_log/{model}")
    file_handler = logging.FileHandler(log_file)
    logger.addHandler(file_handler)
    try:
        problem_desc = bug_report["problem_statement"]
        content = KEYWORD_EXTRACT_PROMPT.format(bug_report=problem_desc)
        prompt = [
            {
                "role": "system",
                "content": "You are an assistant who analyzes the bug report of a Python project."
            },
            {
                "role": "user",
                "content": content
            }
        ]

        keywords = query_chat_llm(prompt, model, temperature=0.0)
        
        logger.info(keywords)

        if "```python" in keywords:
            keywords = keywords.split("```python")[1].split("```")[0].strip()
        elif "```" in keywords:
            keywords = keywords.split("```")[1].strip()
        else:
            logger.info(f"{id} format error")
            keywords = keywords.split("[")[1].split("]")[0]
            keywords = f"[{keywords}]"
            
        # Remove comment parts
        s_clean = re.sub(r"#.*", "", keywords)  # Remove # and the content following it
        keywords = ast.literal_eval(s_clean)

        logger.info(f"Extracted keywords: {keywords}")
    
    except Exception as e:
        logging.error(f"Error extracting keywords for {id}: {e}")
        keywords = None
    finally:
        logger.removeHandler(file_handler)
    return keywords
        
if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--keywords_path", type=str, default="./retrieval_results/code/keywords_gpt.json", help="Path to save the extracted keywords.")
    parser.add_argument("--model", type=str, default="gpt-4o", help="LLM model to use.")
    parser.add_argument("--swt", action="store_true", default=False, help="Whether to use the SWT-bench dataset.")
    parser.add_argument("--tdd", action="store_true", default=False, help="Whether to use the TDD-bench dataset.")
    args = parser.parse_args()
    model = args.model
    keywords_path = args.keywords_path
    use_swt = args.swt
    use_tdd = args.tdd

    with open("swt.txt", "r") as f:
        swt = f.read().strip().split("\n")
    with open("tdd.txt", "r") as f:
        tdd = f.read().strip().split("\n")
    if use_swt:
        ds = load_dataset("SWE-bench/SWE-bench_Lite")["test"]
    elif use_tdd:
        ds = load_dataset("princeton-nlp/SWE-bench_Verified")["test"]
    all_keywords = {}
    if os.path.exists(keywords_path):
        with open(keywords_path, "r") as f:
            all_keywords = json.load(f)
    flag = False
    try:
        for bug_report in tqdm(ds):
            id = bug_report["instance_id"]
            if use_swt and id not in swt:
                continue
            if use_tdd and id not in tdd:
                continue
            if id in all_keywords:
                continue
            keywords = extract_keywords(bug_report, model)
            all_keywords[bug_report["instance_id"]] = keywords
    finally:
        with open(keywords_path, "w") as f:
            json.dump(all_keywords, f, indent=4)