"""Budgeted generation; the controller sees buggy execution only."""

from dataclasses import asdict
import hashlib
import json
from pathlib import Path

from scripts.brt_harness.execution import apply_patch, snapshot
from scripts.brt_harness.materialize import materialize, parse_candidate


SYSTEM = """Write a new bug reproduction test as a sibling of an existing test.
Use the selected host's exact module/class style and base-revision APIs. Reuse
its fixtures and helpers. Reference tests provide usage and assertion patterns;
derive the correct behavior from the issue, not the buggy implementation.
Create temporary files/directories explicitly. Use version-appropriate CLI exit
handling. Assert outputs, state, warnings, or precise exit status when appropriate;
merely accepting any exception/SystemExit does not distinguish a bug.
Return ONLY a JSON object containing trigger, expected_behavior,
distinguishing_assertion, additional_imports, helpers, and test_code (all strings).
test_code must contain exactly one uniquely named test_brt_ function, including
self if the selected host is a class. Do not emit a class wrapper or infer another
destination. additional_imports contains ordinary module imports; helpers contains
module-level definitions/constants with unique brt_ names. Preserve helper scope.
Reuse existing names without redefining them. Do not modify production code,
existing tests, or skip/xfail the generated test. For repair preserve the prior
expected_behavior string exactly. Change setup or the trigger/assertion when the
feedback warrants it; never weaken expected behavior to agree with buggy output.
You may include a normal-input control inside the test when justified by the issue.
Your input contains no known fix or fixed-version execution feedback.
"""


def save_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    temporary.replace(path)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def feedback_for(result, issue):
    status = result['status']
    diagnostic = result.get('diagnostic', '')
    setup_exceptions = ('NameError', 'ImportError', 'ModuleNotFoundError', 'AttributeError', 'TypeError')
    unrelated = next((name for name in setup_exceptions if name in diagnostic and name not in issue), None)
    if status == 'failed' and unrelated:
        action = 'repair_setup'
    elif status == 'failed':
        action = 'candidate_failure'
    elif status == 'passed':
        action = 'revise_trigger'
    elif status in {'collection_error', 'setup_error'}:
        action = 'retrieve_support'
    elif status == 'timeout':
        action = 'simplify_or_switch'
    else:
        action = 'repair_setup'
    # This is a routing heuristic, never a success verdict.
    return dict(action=action, status=status, diagnostic=diagnostic[-10000:])


def generate_instance(row, repo, context, code_context, runner, complete, output,
                      *, attempts=4, temperature=0.7, max_hosts=2, max_lookups=3):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    hosts = context.hosts(row['problem_statement'], max_hosts)
    calibrations = {}
    for position, host in enumerate(hosts):
        folder = output / 'hosts' / str(position + 1)
        calibration_path = folder / 'execution.json'
        if calibration_path.exists():
            calibrated = json.loads(calibration_path.read_text(encoding='utf-8'))
        else:
            with snapshot(repo, row['base_commit']) as work:
                selector = host.file + '::' + host.reference.replace('.', '::')
                calibrated = runner.run(work, selector, folder)
                save_json(calibration_path, calibrated)
        calibrations[host.id] = calibrated
    # A base test may itself expose the issue. Keep failing references, while
    # preferring hosts whose environment and setup have demonstrably worked.
    hosts.sort(key=lambda host: {'passed': 0, 'failed': 1}.get(calibrations[host.id]['status'], 2))
    host_index, extra, lookups = 0, [], 0
    previous, feedback, expected = None, None, None
    history, frozen, seen = [], [], {}
    # Each attempt executes in a fresh export; tests cannot contaminate later attempts.
    for number in range(1, attempts + 1):
        host = hosts[host_index]
        package = context.package(host, extra)
        package['calibration'] = {key: calibrations[host.id][key] for key in ('status', 'diagnostic')}
        package['calibration']['diagnostic'] = package['calibration']['diagnostic'][-4000:]
        user = dict(issue=row['problem_statement'], test_context=package,
                    production_context=code_context, previous_candidate=previous,
                    feedback=feedback)
        messages = [dict(role='system', content=SYSTEM), dict(role='user', content=json.dumps(user))]
        folder = output / 'attempts' / str(number)
        save_json(folder / 'prompt.json', messages)
        save_json(folder / 'context.json', package)
        # An interrupted response is reused; resume never spends a second call for
        # a response already persisted under the same manifest.
        answer_path = folder / 'response.txt'
        if answer_path.exists():
            answer = answer_path.read_text(encoding='utf-8')
        else:
            answer = complete(messages, temperature)
            if not isinstance(answer, str) or not answer.strip():
                raise RuntimeError('LLM request failed; rerun to retry this unfinished attempt')
            answer_path.write_text(answer, encoding='utf-8')
        candidate = None
        try:
            candidate = parse_candidate(answer)
            if expected is not None and candidate['expected_behavior'] != expected:
                raise ValueError('Repair changed expected_behavior; preserve the issue contract exactly')
            if expected is None:
                expected = candidate['expected_behavior']
            built = materialize(context.sources[host.file], host, candidate)
            patch_digest = digest(built['patch'])
            (folder / 'candidate.patch').write_text(built['patch'], encoding='utf-8', newline='\n')
            with snapshot(repo, row['base_commit']) as work:
                apply_patch(work, built['patch'])
                result_path = folder / 'execution.json'
                if result_path.exists():
                    result = json.loads(result_path.read_text(encoding='utf-8'))
                elif patch_digest in seen:
                    result = dict(seen[patch_digest], execution_reused=True)
                    save_json(result_path, result)
                else:
                    result = runner.run(work, built['selector'], folder / 'execution')
                    save_json(result_path, result)
                seen[patch_digest] = result
            routed = feedback_for(result, row['problem_statement'])
            # Valid call failures are provisional. Prefer those not explained by
            # common setup mistakes, then earlier attempts; no gold outcome used.
            if result['status'] == 'failed':
                frozen.append(dict(attempt=number, host=asdict(host), candidate=candidate,
                                   patch=built['patch'], selector=built['selector'],
                                   priority=int(routed['action'] == 'candidate_failure')))
        except (SyntaxError, ValueError) as exc:
            result = dict(status='invalid_candidate', diagnostic=f'{type(exc).__name__}: {exc}')
            routed = dict(action='repair_setup', **result)
        save_json(folder / 'result.json', dict(execution=result, feedback=routed))
        history.append(dict(attempt=number, host=host.id, status=result['status'], action=routed['action'],
                            execution_reused=result.get('execution_reused', False)))
        previous = candidate
        feedback = routed
        if routed['action'] == 'candidate_failure':
            break
        if lookups < max_lookups:
            additions = [sid for sid in context.feedback_symbols(host, routed) if sid not in extra]
            extra.extend(additions[:max_lookups - lookups])
            lookups = len(extra)
        # Repair once locally, then explore another host. A passing test needs
        # a different trigger/example rather than repeated syntax-level repair.
        if len(hosts) > 1 and (routed['action'] in {'revise_trigger', 'simplify_or_switch'}
                               or number % 2 == 0):
            host_index = (host_index + 1) % len(hosts)
        save_json(output / 'progress.json', dict(history=history, lookups=lookups))
    frozen.sort(key=lambda value: (-value['priority'], value['attempt']))
    selected = frozen[0] if frozen else None
    result = dict(instance_id=row['instance_id'], attempts_used=len(history),
                  support_symbols_added=extra, history=history, selected=selected,
                  host_calibration={sid: result['status'] for sid, result in calibrations.items()},
                  execution_count=len(calibrations) + sum(item['status'] != 'invalid_candidate'
                      and not item['execution_reused'] for item in history),
                  selection_uses_fixed_feedback=False)
    if selected:
        (output / 'selected.patch').write_text(selected['patch'], encoding='utf-8', newline='\n')
    save_json(output / 'generation.json', result)
    return result
