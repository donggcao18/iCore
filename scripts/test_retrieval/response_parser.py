"""Parse model-selected [file, test name] pairs without executing model output."""

import ast
import io
import re
import tokenize


def parse_test_selection(content):
    if not isinstance(content, str) or not content.strip():
        raise ValueError('The assistant returned no test-selection text.')
    content = re.sub(r'<think>.*?</think>', '', content, flags=re.DOTALL)
    blocks = re.findall(r'```(?:python|json)?\s*\n?(.*?)```', content, re.DOTALL)
    sources = blocks or [content]
    selections = []
    for source in sources:
        # Tokenization respects quotes, escapes, and comments containing brackets.
        # Start at each opening bracket so introductory prose is harmless.
        for match in re.finditer(r'\[', source):
            fragment = source[match.start():]
            lines = fragment.splitlines(keepends=True)
            depth = 0
            try:
                for token in tokenize.generate_tokens(io.StringIO(fragment).readline):
                    if token.type != tokenize.OP:
                        continue
                    if token.string == '[':
                        depth += 1
                    elif token.string == ']':
                        depth -= 1
                        if depth == 0:
                            end = sum(len(line) for line in lines[:token.end[0] - 1]) + token.end[1]
                            value = ast.literal_eval(fragment[:end])
                            if isinstance(value, list) and all(
                                isinstance(pair, (list, tuple)) and len(pair) == 2
                                and all(isinstance(item, str) and item.strip() for item in pair)
                                for pair in value
                            ):
                                value = [list(pair) for pair in value]
                                if value not in selections:
                                    selections.append(value)
                            break
            except (SyntaxError, ValueError, tokenize.TokenError, IndentationError):
                continue
    if not selections:
        raise ValueError('No valid list of [file_path, test_name] pairs in the assistant response.')
    if len(selections) > 1:
        raise ValueError('Multiple different test-selection lists found; inspect the saved response.')
    return selections[0]
