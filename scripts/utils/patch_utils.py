"""Validate benchmark production fixes in Git or plain unified-diff format."""

import re
import subprocess


def prepare_reference_patch(patch, instance_id='unknown instance'):
    if not isinstance(patch, str) or not patch.strip():
        raise ValueError(f'{instance_id}: missing reference production fix in patch.')
    text = patch.lstrip('\r\n')
    if not (text.startswith('diff --git ') or re.match(r'--- [^\n]+\n\+\+\+ [^\n]+(?:\n|$)', text)):
        raise ValueError(f'{instance_id}: reference production fix must be a Git or unified diff.')
    if not text.endswith('\n'):
        text += '\n'
    # SWT production fixes can contain stale hunk line counts. Recount from
    # the actual diff body; this read-only check does not apply any changes.
    result = subprocess.run(
        ['git', 'apply', '--recount', '--numstat', '-'],
        input=text.encode('utf-8'), capture_output=True, timeout=30,
    )
    if result.returncode or not result.stdout.strip():
        detail = result.stderr.decode('utf-8', errors='replace').strip()
        raise ValueError(f'{instance_id}: invalid reference production fix: {detail or "empty diff"}')
    return text
