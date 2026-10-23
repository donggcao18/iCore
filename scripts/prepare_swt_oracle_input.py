"""Prepare repository-specific SWT rows with verified complete production fixes.

Keep SWT instance selection, issue text, and reference test patches. Restore a
production patch only when it equals the upstream fix or its exact interior
after the first and last lines were removed by the SWT export source.
"""

import argparse
import csv
import hashlib
import json
from pathlib import Path

from scripts.export_swt_verified import DATASET
from scripts.test_retrieval.extract_oracle import repository_name
from scripts.utils.patch_utils import prepare_reference_patch


REFERENCE_DATASET = 'SWE-bench/SWE-bench_Verified'
ROOT = Path(__file__).resolve().parents[1]


def restore_rows(rows, reference_rows, repo):
    selected = [dict(row) for row in rows if row['repo'] == repo]
    ids = {row['instance_id'] for row in selected}
    if not selected or len(ids) != len(selected):
        raise ValueError(f'Expected nonempty unique SWT instances for {repo}')
    references = {}
    for row in reference_rows:
        if row['instance_id'] in ids:
            if row['instance_id'] in references:
                raise ValueError(f'Duplicate reference instance: {row["instance_id"]}')
            references[row['instance_id']] = row
    records = []
    for row in selected:
        bug_id = row['instance_id']
        if row.get('source_dataset') != DATASET or not row.get('test_patch', '').startswith('diff --git '):
            raise ValueError(f'{bug_id}: input must be normalized by scripts.export_swt_verified')
        reference = references.get(bug_id)
        if reference is None:
            raise ValueError(f'{bug_id}: missing from {REFERENCE_DATASET}')
        for field in ('repo', 'base_commit', 'version', 'environment_setup_commit'):
            if str(row.get(field, '')) != str(reference.get(field, '')):
                raise ValueError(f'{bug_id}: reference {field} does not match the SWT row')
        original, complete = row['patch'], reference['patch']
        # Compare lines exactly apart from a final newline; never trim source indentation.
        old_lines, full_lines = original.splitlines(), complete.splitlines()
        if old_lines == full_lines:
            repair = 'already_complete'
        elif len(full_lines) > 2 and old_lines == full_lines[1:-1]:
            repair = 'restored_first_and_last_upstream_lines'
        else:
            raise ValueError(f'{bug_id}: production patch is neither the full reference nor its exact truncated interior')
        prepare_reference_patch(complete, bug_id)
        row['patch'] = complete
        records.append({
            'instance_id': bug_id, 'base_commit': row['base_commit'], 'repair': repair,
            'original_patch_sha256': hashlib.sha256(original.encode()).hexdigest(),
            'complete_patch_sha256': hashlib.sha256(complete.encode()).hexdigest(),
        })
    return selected, records


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', required=True, type=repository_name)
    parser.add_argument('--input', type=Path, default=ROOT / 'data/swt-bench-verified/test.csv')
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--reference-json', type=Path,
                        help='Optional saved upstream row list; otherwise download via datasets.')
    args = parser.parse_args(argv)
    if args.input.resolve() == args.output.resolve():
        parser.error('--output must differ from the original SWT CSV')
    csv.field_size_limit(10_000_000)
    with args.input.open(encoding='utf-8-sig', newline='') as handle:
        rows = list(csv.DictReader(handle))
    if args.reference_json:
        references = json.loads(args.reference_json.read_text(encoding='utf-8'))
    else:
        from datasets import load_dataset
        references = load_dataset(REFERENCE_DATASET, split='test')
    selected, records = restore_rows(rows, references, args.repo)
    manifest = {
        'repo': args.repo, 'source_dataset': DATASET, 'reference_dataset': REFERENCE_DATASET,
        'source_csv_sha256': hashlib.sha256(args.input.read_bytes()).hexdigest(),
        'instances': len(selected), 'swt_test_patches_preserved': True, 'records': records,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    # Validate every record before replacing a prior prepared CSV.
    with args.output.open('w', encoding='utf-8', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=list(selected[0]))
        writer.writeheader()
        writer.writerows(selected)
    manifest['prepared_csv_sha256'] = hashlib.sha256(args.output.read_bytes()).hexdigest()
    args.output.with_suffix('.manifest.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    print(f'Prepared {len(selected)} {args.repo} instances: {args.output}', flush=True)
    print('Versions: ' + ', '.join(sorted({row['version'] for row in selected})), flush=True)


if __name__ == '__main__':
    main()
