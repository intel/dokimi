# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
"""Release policy decisions and paired scan statistics for shipped products."""
from statistics import mean


def evaluate(required: list[str], identity: dict, observations: list[dict]) -> dict:
    checks = {}
    for check in required:
        matches = [o for o in observations if o.get('task') == check and o.get('origin') == 'contract'
                   and o.get('arm') == 'release' and o.get('identity') == identity]
        outcome = 'INCONCLUSIVE'
        if len(matches) == 1:
            row = matches[0]
            evidence = row.get('evidence') or {}
            code = evidence.get('verifier_exit') if isinstance(evidence, dict) else None
            expected = {0: 'PASS', 10: 'FAIL'}.get(code) if type(code) is int else None
            if expected and row.get('outcome') == expected:
                outcome = expected
        checks[check] = outcome
    verdict = ('FAIL' if 'FAIL' in checks.values() else 'PASS' if checks and set(checks.values()) == {'PASS'}
               else 'INCONCLUSIVE')
    return {'verdict': verdict, 'checks': checks}


def score(groups: dict, tasks: dict, repeats: int, rows: list[dict], arm: str) -> dict:
    observed = claimed = false = 0
    details = {}
    total_score = 0.0
    for group, weight in groups.items():
        rates = []
        counts = {}
        for name, task in tasks.items():
            if task['group'] != group:
                continue
            attempts = [r for r in rows if r['task'] == name and r['arm'] == arm and r['outcome'] in ('PASS', 'FAIL')]
            successes = sum(r['outcome'] == 'PASS' for r in attempts)
            counts[name] = {'successes': successes, 'measured': len(attempts), 'planned': repeats}
            observed += len(attempts)
            claims = [r for r in attempts if r.get('claimed_success') is True]
            claimed += len(claims)
            false += sum(r['outcome'] == 'FAIL' for r in claims)
            if attempts:
                rates.append(successes / len(attempts))
        normalized = weight / sum(groups.values())
        details[group] = {'weight': normalized, 'scenarios': counts}
        if rates:
            total_score += 10 * normalized * mean(rates)
    planned = len(tasks) * repeats
    return {'score': total_score if observed == planned else None, 'coverage': [observed, planned],
            'groups': details, 'false_success': [false, claimed]}
