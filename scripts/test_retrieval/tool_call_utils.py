"""Validate tool calls and recover saved conversations before replaying them."""

import json
import logging
from pathlib import Path


class InvalidToolCall(ValueError):
    pass


def validate_tool_calls(calls):
    if not isinstance(calls, list) or not calls:
        raise InvalidToolCall('tool_calls must be a nonempty list')
    seen = set()
    for index, call in enumerate(calls):
        if not isinstance(call, dict):
            raise InvalidToolCall(f'tool_calls[{index}] must be an object')
        call_id = call.get('id')
        if not isinstance(call_id, str) or not call_id.strip() or call_id in seen:
            raise InvalidToolCall(f'tool_calls[{index}].id must be a nonempty, unique string')
        seen.add(call_id)
        function = call.get('function')
        if call.get('type') != 'function' or not isinstance(function, dict):
            raise InvalidToolCall(f'tool_calls[{index}] must describe a function')
        name = function.get('name')
        if not isinstance(name, str) or not name.strip():
            raise InvalidToolCall(f'tool_calls[{index}].function.name must be a nonempty string')
        arguments = function.get('arguments')
        try:
            parsed = json.loads(arguments) if isinstance(arguments, str) else None
        except ValueError:
            parsed = None
        if not isinstance(parsed, dict):
            raise InvalidToolCall(f'tool_calls[{index}].function.arguments must encode a JSON object')


def load_tool_history(messages_path):
    """Back up malformed history and resume from before the invalid tool turn."""
    path = Path(messages_path)
    original = path.read_bytes()
    messages = json.loads(original.decode('utf-8'))
    if not isinstance(messages, list) or any(not isinstance(m, dict) for m in messages):
        raise ValueError(f'Expected a conversation list in {path}')
    for index, message in enumerate(messages):
        if message.get('role') != 'assistant' or not message.get('tool_calls'):
            continue
        try:
            validate_tool_calls(message['tool_calls'])
        except InvalidToolCall as error:
            number = 0
            while True:
                suffix = '' if number == 0 else f'.{number}'
                backup = path.with_name(f'{path.stem}.invalid_tool_calls{suffix}.json')
                try:
                    with backup.open('xb') as handle:
                        handle.write(original)
                    break
                except FileExistsError:
                    number += 1
            messages = messages[:index]
            path.write_text(json.dumps(messages, indent=4), encoding='utf-8')
            logging.warning('Recovered malformed tool history in %s (%s). Original saved to %s.',
                            path, error, backup)
            break
    return messages
