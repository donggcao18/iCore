"""Parse model-selected [file, test name] pairs without executing model output."""

import ast
import io
import logging
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


def ensure_test_selection(messages, request, save, topk=5, max_repairs=2):
    """Validate a terminal reply, retaining history during bounded format repairs.

    request(messages) returns an assistant message with tools disabled. save
    checkpoints the conversation before validation and after each repair reply.
    Invalid output must never be silently interpreted as an empty selection.
    """
    save(messages)
    for attempt in range(max_repairs + 1):
        final = messages[-1]
        if final.get('role') != 'assistant' or final.get('tool_calls'):
            raise ValueError('Expected a final assistant answer without tool calls.')
        try:
            return parse_test_selection(final.get('content'))
        except ValueError as error:
            if attempt == max_repairs:
                raise ValueError(
                    f'Invalid test selection after {max_repairs} formatting retries: {error}'
                ) from error
            logging.warning('Invalid test selection (%s); formatting retry %s/%s.',
                            error, attempt + 1, max_repairs)
            messages.append({
                'role': 'user',
                'content': (
                    f'Your final answer could not be parsed: {error}\n'
                    'Using the bug report and test evidence already in this conversation, '
                    'return your selected tests as ONLY a JSON array of '
                    '[file_path, test_name] pairs. Both values must be nonempty strings. '
                    f'Include at most {topk} pairs, ranked by relevance. Example: '
                    '[["tests/test_example.py", "TestExample.test_case"]]. '
                    'Use actual paths and names from the evidence, not the example. '
                    'Do not add explanations, ellipses, or tool calls. Do not invent tests. '
                    'Return [] only if you determined that no relevant tests exist; '
                    'a formatting failure does not mean no relevant tests exist.'
                ),
            })
            messages.append(request(messages))
            save(messages)
