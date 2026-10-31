# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""``repo compare --ledger``: rule verdicts over hand-written batch directories. No docker, no LLM."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from dokimi.cli import build_parser
from dokimi.repos.ledger import KEEP, REVIEW, INSUFFICIENT, LedgerError, evaluate, format_ledger, load_ledger

TODAY = date(2026, 10, 31)
BASE, ARM = "guidance:none", "guidance:rules"


def _cell(batch: Path, task: str, arm: str, *, success: bool | None = None, score: float | None = None,
          model: str = "m-1") -> None:
    rd = batch / f"{task}__{arm.replace(':', '-')}__{model}__r1"
    rd.mkdir(parents=True)
    score_task = score is not None
    grade = {"status": "graded", "pass": None if score_task else success, "success": None if score_task else success,
             "score": score, "score_only": score_task, "reasons": []}
    rec = {"task": task, "arm": arm, "model": model, "rep": 1, "score_task": score_task, "grade": grade,
           "harness_version": "1.0", "score_max": 1.0 if score_task else None, "model_resolved": None,
           "agent": {"cost_usd": 0.1, "num_turns": 10, "timed_out": False, "is_error": False, "rc": None,
                     "over_budget": False, "network": "none"}}
    (rd / "meta.json").write_text("{}")
    (rd / "result.json").write_text(json.dumps(rec))
    (rd / "grade.json").write_text(json.dumps({"metrics": {}}))


def _gate_batch(root: Path, outcomes: dict[str, tuple[bool, bool]], model: str = "m-1") -> Path:
    """One batch; ``outcomes`` maps task -> (baseline passes, rules arm passes)."""
    batch = root / "run"
    for task, (base, treat) in outcomes.items():
        _cell(batch, task, BASE, success=base, model=model)
        _cell(batch, task, ARM, success=treat, model=model)
    return batch


def _ledger(path: Path, rules: list[dict], **policy) -> Path:
    data: dict = {"schema_version": "repo_ledger.v1", "rules": rules}
    if policy:
        data["policy"] = policy
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _rule(rid: str, cases: list[str], reviewed: str = "2026-10-30", **extra) -> dict:
    return {"id": rid, "hypothesis": f"{rid} changes the answer", "arm": ARM, "cases": cases,
            "reviewed": reviewed, **extra}


def _ids(tasks: str, n: int) -> list[str]:
    return [f"{tasks}_{i}" for i in range(n)]


def _verdicts(result: dict) -> dict[str, str]:
    return {(e["rule"], e["model"]): e["verdict"] for e in result["rules"]}


def test_a_valid_ledger_loads_with_the_policy_defaults(tmp_path):
    path = _ledger(tmp_path / "ledger.json", [_rule("R1", ["t1"], source="AGENTS.md#commits", rule="Say why.")])
    ledger = load_ledger(path)
    assert [r.id for r in ledger.rules] == ["R1"]
    assert (ledger.alpha, ledger.review_within_days) == (0.05, 180)
    assert ledger.rules[0].reviewed == date(2026, 10, 30)
    assert ledger.rules[0].source == "AGENTS.md#commits"


@pytest.mark.parametrize("bad", [
    {"id": "R1", "hypothesis": "h", "arm": ARM, "cases": ["t1"], "reviewed": "2026-02-30"},
    {"id": "R1", "hypothesis": "h", "arm": "guidance:rules/", "cases": ["t1"], "reviewed": "2026-01-01"},
    {"id": "R1", "hypothesis": "h", "arm": ARM, "cases": [], "reviewed": "2026-01-01"},
    {"id": "R1", "hypothesis": "h", "arm": ARM, "cases": ["t1", "t1"], "reviewed": "2026-01-01"},
    {"id": "R 1", "hypothesis": "h", "arm": ARM, "cases": ["t1"], "reviewed": "2026-01-01"},
    {"id": "R1", "arm": ARM, "cases": ["t1"], "reviewed": "2026-01-01"},
    {"id": "R1", "hypothesis": "h", "arm": ARM, "cases": ["t1"], "reviewed": "2026-01-01", "verdict": "cut"},
], ids=["impossible-date", "bad-selector", "no-cases", "repeated-case", "id-with-space", "no-hypothesis",
        "unknown-key"])
def test_a_rule_that_does_not_validate_is_refused(tmp_path, bad):
    path = _ledger(tmp_path / "ledger.json", [bad])
    with pytest.raises(LedgerError):
        load_ledger(path)


def test_a_repeated_rule_id_is_refused(tmp_path):
    path = _ledger(tmp_path / "ledger.json", [_rule("R1", ["t1"]), _rule("R1", ["t2"])])
    with pytest.raises(LedgerError, match="R1"):
        load_ledger(path)


def test_an_unreadable_ledger_is_refused(tmp_path):
    path = tmp_path / "ledger.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(LedgerError, match="cannot read"):
        load_ledger(path)


def test_all_cases_improved_is_keep_and_all_worse_is_review(tmp_path):
    tasks = _ids("t", 8)
    batch = _gate_batch(tmp_path, {t: (False, True) for t in tasks} | {u: (True, False) for u in _ids("u", 8)})
    ledger = load_ledger(_ledger(tmp_path / "ledger.json", [_rule("up", tasks), _rule("down", _ids("u", 8))]))
    result = evaluate(ledger, [batch], BASE, today=TODAY)
    assert _verdicts(result) == {("up", "m-1"): KEEP, ("down", "m-1"): REVIEW}
    up = result["rules"][0]
    assert (up["up"], up["down"], up["paired"]) == (8, 0, 8)
    assert (up["baseline_mean"], up["treatment_mean"]) == (0.0, 1.0)
    assert up["p_holm"] == pytest.approx(2 * 2 / 2**8, abs=1e-5)  # Holm over the two rules; p is rounded to 6 places


def test_few_changed_cases_are_insufficient_evidence_and_the_reason_says_why(tmp_path):
    tasks = _ids("t", 3)
    batch = _gate_batch(tmp_path, {t: (False, True) for t in tasks})
    result = evaluate(load_ledger(_ledger(tmp_path / "ledger.json", [_rule("R1", tasks)])), [batch], BASE,
                      today=TODAY)
    entry = result["rules"][0]
    assert entry["verdict"] == INSUFFICIENT
    assert "no outcome reaches p < 0.05" in entry["reason"]


def test_holm_corrects_the_rules_together(tmp_path):
    # each rule alone reaches p = 2/64 = 0.031 (six cases all improved); two rules together give Holm 0.0625
    first, second = _ids("a", 6), _ids("b", 6)
    batch = _gate_batch(tmp_path, {t: (False, True) for t in first + second})
    alone = evaluate(load_ledger(_ledger(tmp_path / "one.json", [_rule("A", first)])), [batch], BASE, today=TODAY)
    both = evaluate(load_ledger(_ledger(tmp_path / "two.json", [_rule("A", first), _rule("B", second)])),
                    [batch], BASE, today=TODAY)
    assert alone["rules"][0]["verdict"] == KEEP
    assert [e["verdict"] for e in both["rules"]] == [INSUFFICIENT, INSUFFICIENT]
    assert both["rules"][0]["p_holm"] == pytest.approx(2 * alone["rules"][0]["p"])
    assert both["family"] == 2


def test_a_missing_case_is_listed_and_the_rest_are_measured(tmp_path):
    tasks = _ids("t", 8)
    batch = _gate_batch(tmp_path, {t: (False, True) for t in tasks})
    rule = _rule("R1", tasks + ["never_ran"])
    entry = evaluate(load_ledger(_ledger(tmp_path / "ledger.json", [rule])), [batch], BASE, today=TODAY)["rules"][0]
    assert entry["missing"] == ["never_ran"]
    assert (entry["linked"], entry["paired"], entry["verdict"]) == (9, 8, KEEP)


def test_a_rule_with_no_case_that_ran_is_not_measured(tmp_path):
    batch = _gate_batch(tmp_path, {"t1": (False, True)})
    entry = evaluate(load_ledger(_ledger(tmp_path / "ledger.json", [_rule("R1", ["ghost"])])), [batch], BASE,
                     today=TODAY)["rules"][0]
    assert entry["verdict"] == INSUFFICIENT
    assert entry["reason"].startswith("not measured")
    assert entry["missing"] == ["ghost"]


def test_an_arm_with_no_cell_on_the_linked_cases_is_not_measured(tmp_path):
    batch = _gate_batch(tmp_path, {"t1": (False, True)})
    _cell(batch, "t2", BASE, success=False)  # the baseline ran t2, the rules arm did not
    entry = evaluate(load_ledger(_ledger(tmp_path / "ledger.json", [_rule("R1", ["t2"])])), [batch], BASE,
                     today=TODAY)["rules"][0]
    assert entry["verdict"] == INSUFFICIENT
    assert "not measured" in entry["reason"]


def test_a_tie_on_every_linked_case_has_nothing_to_test(tmp_path):
    tasks = _ids("t", 4)
    batch = _gate_batch(tmp_path, {t: (True, True) for t in tasks})
    entry = evaluate(load_ledger(_ledger(tmp_path / "ledger.json", [_rule("R1", tasks)])), [batch], BASE,
                     today=TODAY)["rules"][0]
    assert entry["verdict"] == INSUFFICIENT
    assert "every linked case ties" in entry["reason"]


def test_gate_and_score_cases_in_one_rule_are_refused_as_mixed(tmp_path):
    batch = _gate_batch(tmp_path, {t: (False, True) for t in _ids("g", 8)})
    for task in _ids("s", 8):
        _cell(batch, task, BASE, score=0.2)
        _cell(batch, task, ARM, score=0.9)
    rule = _rule("R1", _ids("g", 8) + _ids("s", 8))
    results = evaluate(load_ledger(_ledger(tmp_path / "ledger.json", [rule])), [batch], BASE, today=TODAY)["rules"]
    assert {e["measure"] for e in results} == {"gate", "score"}
    assert all(e["verdict"] == INSUFFICIENT and "mix gate and score" in e["reason"] for e in results)


def test_a_score_rule_is_measured_on_score(tmp_path):
    batch = _score_batch(tmp_path)
    entry = evaluate(load_ledger(_ledger(tmp_path / "ledger.json", [_rule("R1", _ids("s", 8))])), [batch], BASE,
                     today=TODAY)["rules"][0]
    assert entry["measure"] == "score"
    assert entry["verdict"] == KEEP
    assert entry["delta"]["mean"] == pytest.approx(0.7)


def _score_batch(root: Path) -> Path:
    batch = root / "run"
    for task in _ids("s", 8):
        _cell(batch, task, BASE, score=0.2)
        _cell(batch, task, ARM, score=0.9)
    return batch


def test_results_are_per_model(tmp_path):
    batch = _gate_batch(tmp_path, {t: (False, True) for t in _ids("t", 8)}, model="m-1")
    for task in _ids("t", 8):  # a second model where the rule does nothing
        _cell(batch, task, BASE, success=True, model="m-2")
        _cell(batch, task, ARM, success=True, model="m-2")
    result = evaluate(load_ledger(_ledger(tmp_path / "ledger.json", [_rule("R1", _ids("t", 8))])), [batch], BASE,
                      today=TODAY)
    assert _verdicts(result) == {("R1", "m-1"): KEEP, ("R1", "m-2"): INSUFFICIENT}


def test_a_review_older_than_the_policy_is_stale_but_keeps_its_verdict(tmp_path):
    tasks = _ids("t", 8)
    batch = _gate_batch(tmp_path, {t: (False, True) for t in tasks})
    path = _ledger(tmp_path / "ledger.json", [_rule("R1", tasks, reviewed="2026-01-01")], review_within_days=180)
    entry = evaluate(load_ledger(path), [batch], BASE, today=TODAY)["rules"][0]
    assert entry["stale"] is True
    assert entry["days_since_review"] == (TODAY - date(2026, 1, 1)).days
    assert entry["verdict"] == KEEP
    assert any("re-review" in w for w in entry["warnings"])


def test_a_recent_review_is_not_stale(tmp_path):
    tasks = _ids("t", 8)
    batch = _gate_batch(tmp_path, {t: (False, True) for t in tasks})
    entry = evaluate(load_ledger(_ledger(tmp_path / "ledger.json", [_rule("R1", tasks)])), [batch], BASE,
                     today=TODAY)["rules"][0]
    assert entry["stale"] is False
    assert not any("re-review" in w for w in entry["warnings"])


def test_a_rule_can_name_its_own_baseline(tmp_path):
    tasks = _ids("t", 8)
    batch = _gate_batch(tmp_path, {t: (False, True) for t in tasks})
    _cell(batch, "t_0", "guidance:raw", success=True)  # the rule's baseline has a cell for one of its cases only
    rule = _rule("R1", tasks, baseline="guidance:raw")
    entry = evaluate(load_ledger(_ledger(tmp_path / "ledger.json", [rule])), [batch], BASE, today=TODAY)["rules"][0]
    assert entry["baseline"] == "guidance:raw"
    assert entry["paired"] == 1  # only t_0 has a cell in both arms


def test_format_names_the_verdict_the_hypothesis_and_the_stale_review(tmp_path):
    tasks = _ids("t", 8)
    batch = _gate_batch(tmp_path, {t: (False, True) for t in tasks})
    path = _ledger(tmp_path / "ledger.json", [_rule("R1", tasks, reviewed="2026-01-01")])
    text = format_ledger(evaluate(load_ledger(path), [batch], BASE, today=TODAY, ledger_name="ledger.json"))
    assert "R1: guidance:rules vs guidance:none, model m-1, gate: keep" in text
    assert "hypothesis: R1 changes the answer" in text
    assert "re-review" in text


def test_compare_with_ledger_adds_the_rule_results_to_the_json(tmp_path, capsys):
    tasks = _ids("t", 8)
    batch = _gate_batch(tmp_path, {t: (False, True) for t in tasks})
    path = _ledger(tmp_path / "ledger.json", [_rule("R1", tasks)])
    args = build_parser().parse_args(["repo", "compare", str(batch), "--baseline", BASE, "--ledger", str(path),
                                      "--json"])
    args.func(args)
    data = json.loads(capsys.readouterr().out)
    assert data["ledger"]["ledger"] == str(path)
    assert [(e["rule"], e["verdict"]) for e in data["ledger"]["rules"]] == [("R1", KEEP)]


def test_compare_text_prints_the_rule_section(tmp_path, capsys):
    tasks = _ids("t", 8)
    batch = _gate_batch(tmp_path, {t: (False, True) for t in tasks})
    path = _ledger(tmp_path / "ledger.json", [_rule("R1", tasks)])
    args = build_parser().parse_args(["repo", "compare", str(batch), "--baseline", BASE, "--ledger", str(path)])
    args.func(args)
    out = capsys.readouterr().out
    assert "rule ledger" in out
    assert "R1: guidance:rules vs guidance:none, model m-1, gate: keep" in out


def test_a_bad_ledger_exits_with_usage_error(tmp_path, capsys):
    batch = _gate_batch(tmp_path, {"t1": (False, True)})
    path = _ledger(tmp_path / "ledger.json", [_rule("R1", ["t1"], reviewed="2026-02-30")])
    args = build_parser().parse_args(["repo", "compare", str(batch), "--baseline", BASE, "--ledger", str(path)])
    with pytest.raises(SystemExit) as exc:
        args.func(args)
    assert exc.value.code == 2
    assert "R1" in capsys.readouterr().err
