"""Load local benchmark rows with an explicit, validated instance selection."""

import csv
import os
from pathlib import Path


def load_selected_csv(path, ids_path=None):
    csv.field_size_limit(10_000_000)
    with Path(path).open(encoding='utf-8-sig', newline='') as handle:
        rows = list(csv.DictReader(handle))
    if not rows or not {'instance_id', 'repo', 'base_commit', 'problem_statement'} <= rows[0].keys():
        raise ValueError(f'Empty or invalid benchmark CSV: {path}')
    ids = [row['instance_id'] for row in rows]
    if len(ids) != len(set(ids)):
        raise ValueError(f'Duplicate instance IDs in {path}')
    selection = ids_path or os.getenv('SWT_IDS_FILE')
    if selection:
        selected = Path(selection).read_text(encoding='utf-8-sig').split()
        if not selected or len(selected) != len(set(selected)):
            raise ValueError(f'Empty or duplicate instance selection: {selection}')
        missing = set(selected) - set(ids)
        if missing:
            raise ValueError(f'Instance IDs missing from {path}: {", ".join(sorted(missing))}')
        rows = [row for row in rows if row['instance_id'] in set(selected)]
    return rows
