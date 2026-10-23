"""Compare first/second-generation F2P results using only the standard library."""

import argparse
import csv
import json
from pathlib import Path


RESULT_NAMES = ('execution_results.json', 'execution_result.json', 'execution__result.json')
COLUMNS = ('first_gen', 'second_gen', 'final_result')


def load_results(directory, required=False):
    for name in RESULT_NAMES:
        path = directory / name
        if path.is_file():
            results = json.loads(path.read_text(encoding='utf-8'))
            if not isinstance(results, dict) or any(not isinstance(value, dict) for value in results.values()):
                raise ValueError(f'Expected instance -> candidate -> result mappings: {path}')
            return results, path
    if required:
        raise FileNotFoundError(f'No execution result file found in {directory}')
    return {}, None


def f2p(candidates):
    if not candidates:
        return None
    # Use the evaluator's recorded F2P verdict. Exception strings are failures.
    return any(isinstance(result, dict) and result.get('success') is True
               for result in candidates.values())


def compare(first, second):
    rows = []
    for instance in sorted(first.keys() | second.keys()):
        original, replacements = first.get(instance, {}), second.get(instance, {})
        # Replace matching candidates; retain originals which were not regenerated.
        final = dict(original, **replacements)
        rows.append({'instance_id': instance, 'first_gen': f2p(original),
                     'second_gen': f2p(replacements), 'final_result': f2p(final)})
    return rows


def statistics(rows):
    summary = {}
    for column in COLUMNS:
        evaluated = sum(row[column] is not None for row in rows)
        passed = sum(row[column] is True for row in rows)
        summary[column] = {'f2p': passed, 'evaluated': evaluated,
                           'none': len(rows) - evaluated,
                           'rate': passed / evaluated if evaluated else None}
    summary['gained'] = sum(row['first_gen'] is False and row['final_result'] is True for row in rows)
    summary['lost'] = sum(row['first_gen'] is True and row['final_result'] is False for row in rows)
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('work_dir', type=Path, help='First-generation run directory.')
    parser.add_argument('--second-dir', type=Path, default=Path('second_generation'),
                        help='Second-generation directory, relative to work_dir or absolute.')
    parser.add_argument('--output', type=Path, help='CSV output; defaults to work_dir/generation_comparison.csv.')
    args = parser.parse_args(argv)
    first, first_path = load_results(args.work_dir, required=True)
    second, second_path = load_results(args.work_dir / args.second_dir)
    rows = compare(first, second)
    print(f'First results: {first_path}')
    print(f'Second results: {second_path or "missing (second_gen is None)"}')
    headers = ('instance_id', *COLUMNS)
    cells = [[str(row[key]) for key in headers] for row in rows]
    widths = [max(len(header), *(len(row[index]) for row in cells))
              for index, header in enumerate(headers)] if cells else list(map(len, headers))
    print(' | '.join(header.ljust(width) for header, width in zip(headers, widths)))
    for row in cells:
        print(' | '.join(value.ljust(width) for value, width in zip(row, widths)))
    summary = statistics(rows)
    print(f'\nInstances: {len(rows)}')
    for column in COLUMNS:
        counts = summary[column]
        rate = f'{counts["rate"]:.1%}' if counts['rate'] is not None else 'N/A'
        print(f'{column}: F2P {counts["f2p"]}/{counts["evaluated"]} ({rate}); None={counts["none"]}')
    print(f'Final vs first: gained={summary["gained"]}, lost={summary["lost"]}')
    output = args.output or args.work_dir / 'generation_comparison.csv'
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('w', newline='', encoding='utf-8') as handle:
        writer = csv.writer(handle)
        writer.writerow(headers)
        writer.writerows(cells)
    print(f'CSV: {output}')


if __name__ == '__main__':
    main()
