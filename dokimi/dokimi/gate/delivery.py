# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
"""Release policy decisions and paired scan statistics for shipped products."""

from statistics import mean


def evaluate(required: list[str], identity: dict, observations: list[dict]) -> dict:
    checks = {}
    for check in required:
        matches = [
            o
            for o in observations
            if o.get("task") == check
            and o.get("origin") == "contract"
            and o.get("arm") == "release"
            and o.get("identity") == identity
        ]
        outcome = "INCONCLUSIVE"
        if len(matches) == 1:
            row = matches[0]
            evidence = row.get("evidence") or {}
            code = evidence.get("verifier_exit") if isinstance(evidence, dict) else None
            expected = {0: "PASS", 10: "FAIL"}.get(code) if type(code) is int else None
            if expected and row.get("outcome") == expected:
                outcome = expected
        checks[check] = outcome
    verdict = (
        "FAIL"
        if "FAIL" in checks.values()
        else "PASS"
        if checks and set(checks.values()) == {"PASS"}
        else "INCONCLUSIVE"
    )
    return {"verdict": verdict, "checks": checks}


def score(groups: dict, tasks: dict, repeats: int, rows: list[dict], arm: str) -> dict:
    observed = 0
    claims = [r for r in rows if r["arm"] == arm and r.get("claimed_success") is True]
    rejected = sum((r.get("evidence") or {}).get("verifier_exit") == 10 for r in claims)
    unknown = sum((r.get("evidence") or {}).get("verifier_exit") not in (0, 10) for r in claims)
    details = {}
    total_score = 0.0
    for group, weight in groups.items():
        rates = []
        counts = {}
        for name, task in tasks.items():
            if task["group"] != group:
                continue
            attempts = [
                r
                for r in rows
                if r["task"] == name and r["arm"] == arm and r["outcome"] in ("PASS", "FAIL")
            ]
            successes = sum(r["outcome"] == "PASS" for r in attempts)
            counts[name] = {"successes": successes, "measured": len(attempts), "planned": repeats}
            observed += len(attempts)
            if attempts:
                rates.append(successes / len(attempts))
        normalized = weight / sum(groups.values())
        details[group] = {"weight": normalized, "scenarios": counts}
        if rates:
            total_score += 10 * normalized * mean(rates)
    planned = len(tasks) * repeats
    return {
        "score": total_score if observed == planned else None,
        "coverage": [observed, planned],
        "groups": details,
        "false_success": [rejected, len(claims)],
        "unverified_claims": unknown,
    }


def compare_pairs(name: str, observations: list[dict], repeats: int) -> dict:
    """Compare corresponding draws while retaining missing cells and their evidence links."""
    outcomes = {arm: {} for arm in ("A", "B")}  # type: dict[str, dict[int, str]]
    evidence = []
    for index, row in enumerate(observations):
        if row["task"] == name:
            outcomes[row["arm"]][row["repeat"]] = row["outcome"]
            evidence.append(f"#/observations/{index}")
    pairs = [(outcomes["A"].get(i), outcomes["B"].get(i)) for i in range(1, repeats + 1)]
    regressions = pairs.count(("PASS", "FAIL"))
    improvements = pairs.count(("FAIL", "PASS"))
    measured = sum(a in ("PASS", "FAIL") and b in ("PASS", "FAIL") for a, b in pairs)
    repeated = repeats > 1 and (regressions == repeats or improvements == repeats)
    return {
        "attempts": [list(pair) for pair in pairs],
        "measured_pairs": measured,
        "planned_pairs": repeats,
        "regressions": regressions,
        "improvements": improvements,
        "consistent_change_across_repeats": repeated,
        "evidence": evidence,
    }
