# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Golden pins of what the gates publish: the strict-run exit codes and stderr, the runner's gate block,
the compare ``regressions`` block, the trust and novelty markdown blocks, and the novelty ``as_dict`` JSON.

Only consumer-visible surfaces are read here, so the same pins hold across a refactor of the gate modules.
A value that moves changes a published artifact or a CI exit code, so it needs its own decision: regenerate
``tests/fixtures/gate_golden.json`` only with a changelog fragment that says what moved."""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
from pathlib import Path
from unittest.mock import patch

import yaml

from dokimi.commands.run import cmd_run
from dokimi.gate.critical_bands import check_critical_bands
from dokimi.gate.novelty_gate import evaluate_novelty, format_novelty_block
from dokimi.gate.trust_gate import evaluate_trust, format_trust_block
from dokimi.runner.compare import compare_snapshots

GOLDEN = Path(__file__).parent / "fixtures" / "gate_golden.json"

SPEC = {
    "weights": {"coverage": 0.4, "freshness_lite": 0.3, "readability": 0.3},
    "metrics": {"freshness_lite": {"max_age_days": 365}, "readability": {"grade_max": 12}},
}


# ── strict runs: exit code, stdout, stderr and the snapshot's gate block ─────────────────────────────────────

def _patches(root: Path):
    return [
        patch("dokimi.runner.run.discover_markdown", return_value=[root / "docs/a.md"]),
        patch("dokimi.runner.run.load_docs", return_value={str(root / "docs/a.md"): "hello world"}),
        patch("dokimi.runner.run.chunk_text", return_value=["c1"]),
        patch("dokimi.runner.run.coverage.score", return_value=0.8),
        patch("dokimi.runner.run.freshness_lite.score", return_value=0.9),
        patch("dokimi.runner.run.readability.score", return_value=0.7),
    ]


def _G(min_score: float) -> dict:
    return {"enabled": True, "min_score": min_score}


def _strict(root: Path, future: dict, strict: bool, extra: dict | None = None) -> dict:
    root.mkdir(parents=True, exist_ok=True)
    spec = dict(SPEC, future=future, **(extra or {}))
    spec_path = root / "spec.yaml"
    spec_path.write_text(yaml.safe_dump(spec), encoding="utf-8")
    out_json = root / "o.json"
    args = argparse.Namespace(root=str(root), spec=str(spec_path), out_json=str(out_json),
                              out_md=str(root / "o.md"), strict=strict)
    out, err = io.StringIO(), io.StringIO()
    patches = _patches(root)
    for p in patches:
        p.start()
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                cmd_run(args)
                code: object = 0
            except SystemExit as exc:
                code = exc.code
            except Exception as exc:  # a bad spec surfaces as the named input error, not a traceback
                code = f"{type(exc).__name__}: {exc}"
    finally:
        for p in patches:
            p.stop()
    snapshot = json.loads(out_json.read_text(encoding="utf-8")) if out_json.exists() else None
    text = lambda s: s.replace(str(root), "<root>")  # noqa: E731
    if isinstance(code, str):
        code = text(code)
    return {
        "exit": code,
        "stdout": text(out.getvalue()),
        "stderr": text(err.getvalue()),
        "gate": snapshot and snapshot["gate"],
    }


def _strict_scenarios(base: Path) -> dict:
    cases = {
        "soft_pass_hard_pass": ({"soft_gate": _G(0.99), "hard_gate": _G(0.1)}, True, None),
        "soft_fail_hard_pass": ({"soft_gate": _G(0.99), "hard_gate": _G(0.1)}, False, None),
        "soft_only_fail_strict": ({"soft_gate": _G(0.1), "hard_gate": _G(0.99)}, True, None),
        "hard_only_fail_strict": ({"hard_gate": _G(0.99)}, True, None),
        "hard_fail_not_strict": ({"hard_gate": _G(0.99)}, False, None),
        "hard_disabled_with_floor": ({"hard_gate": {"enabled": False, "min_score": 0.95}}, True, None),
        "hard_zero_floor": ({"hard_gate": _G(0.0)}, True, None),
        "no_future": ({}, True, None),
        "bands_score_below_violated": ({}, True, {"critical_bands": {"fail_on": [
            {"condition": "score_below", "value": 0.95}]}}),
        "bands_coverage_ok": ({}, True, {"critical_bands": {"fail_on": [
            {"condition": "coverage_below", "value": 0.5}]}}),
        "bands_readability_and_freshness_violated": ({}, True, {"critical_bands": {"fail_on": [
            {"condition": "readability_below", "value": 0.9},
            {"condition": "freshness_below", "value": 0.95}]}}),
        "bands_unknown_condition": ({}, True, {"critical_bands": {"fail_on": [
            {"condition": "nope", "value": 0.5}]}}),
        "bands_missing_condition": ({}, True, {"critical_bands": {"fail_on": [{"value": 0.5}]}}),
        "bands_non_mapping_entry": ({}, True, {"critical_bands": {"fail_on": ["score_below"]}}),
        "bands_not_strict": ({}, False, {"critical_bands": {"fail_on": [
            {"condition": "score_below", "value": 0.95}]}}),
    }
    return {name: _strict(base / name, future, strict, extra)
            for name, (future, strict, extra) in cases.items()}


def _no_document(base: Path) -> dict:
    root = base / "no_docs"
    root.mkdir(parents=True, exist_ok=True)
    out = {}
    for name, future in (("hard_zero", {"hard_gate": _G(0.0)}), ("none", {})):
        spec_path = root / f"{name}.yaml"
        spec_path.write_text(yaml.safe_dump(dict(SPEC, future=future)), encoding="utf-8")
        args = argparse.Namespace(root=str(root), spec=str(spec_path), out_json=str(root / f"{name}.json"),
                                  out_md=str(root / f"{name}.md"), strict=True)
        err = io.StringIO()
        with patch("dokimi.runner.run.discover_markdown", return_value=[]), \
                contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            try:
                cmd_run(args)
                code: object = 0
            except SystemExit as exc:
                code = exc.code
        out[name] = {"exit": code, "stderr": err.getvalue().replace(str(root), "<root>")}
    return out


# ── band spec errors, as the check raises them ───────────────────────────────────────────────────────────────

def _band_errors() -> dict:
    cases = {
        "unknown": {"critical_bands": {"fail_on": [{"condition": "nope", "value": 1}]}},
        "missing": {"critical_bands": {"fail_on": [{"value": 1}]}},
        "non_mapping": {"critical_bands": {"fail_on": [3]}},
    }
    summary = {"score": 0.5, "coverage": 0.5, "freshness_lite": 0.5, "readability": 0.5}
    out = {}
    for name, spec in cases.items():
        try:
            check_critical_bands(summary, spec)
            out[name] = "no error"
        except ValueError as exc:
            out[name] = f"ValueError: {exc}"
    try:
        check_critical_bands({"score": None}, {"critical_bands": {"fail_on": [
            {"condition": "score_below", "value": 0.5}]}})
        out["none_metric"] = "no error"
    except ValueError as exc:
        out["none_metric"] = f"ValueError: {exc}"
    return out


# ── compare: the regressions block (and its absence without a spec) ──────────────────────────────────────────

def _snapshot(score=0.8, coverage=0.7, freshness=0.9, readability=0.6, docs=10, pass_rate=None) -> dict:
    summary = {"docs": docs, "score": score, "coverage": coverage,
               "freshness_lite": freshness, "readability": readability}
    if pass_rate is not None:
        summary["example_pass_rate"] = pass_rate
    return {"summary": summary, "docs": []}


def _compare(base_path: Path, cand_path: Path, spec: dict | None, base_kw: dict, cand_kw: dict) -> dict:
    base_path.write_text(json.dumps(_snapshot(**base_kw)), encoding="utf-8")
    cand_path.write_text(json.dumps(_snapshot(**cand_kw)), encoding="utf-8")
    return compare_snapshots(base_path, cand_path, spec)


def _compare_scenarios(base: Path) -> dict:
    base.mkdir(parents=True, exist_ok=True)
    thresholds = {"thresholds": {"regressions": {
        "score_drop_warn": 0.03, "score_drop_critical": 0.08,
        "metric_drop_warn": 0.05, "metric_drop_critical": 0.12}}}
    custom = {"thresholds": {"regressions": {"score_drop_warn": 0.01, "score_drop_critical": 0.02,
                                              "metric_drop_warn": 0.01, "metric_drop_critical": 0.5}}}
    cases = {
        "no_spec": (None, {"score": 0.9}, {"score": 0.7}),
        "ok": (thresholds, {}, {"score": 0.81}),
        "warn": (thresholds, {}, {"score": 0.75, "coverage": 0.64}),
        "critical": (thresholds, {}, {"score": 0.7, "coverage": 0.5, "readability": 0.5}),
        "pass_rate_warn": (thresholds, {"pass_rate": 0.9}, {"pass_rate": 0.8}),
        "custom_thresholds": (custom, {}, {"score": 0.78, "freshness": 0.85}),
        "improvement_only": (thresholds, {}, {"score": 0.95, "coverage": 0.9}),
    }
    return {name: _compare(base / f"{name}_b.json", base / f"{name}_c.json", spec, b, c)
            for name, (spec, b, c) in cases.items()}


# ── trust: the markdown block ───────────────────────────────────────────────────────────────────────────────

def _ev(with_score=70.0, without_score=50.0, hit=True, agree=None):
    item = {
        "question_id": "q",
        "with_docs": {"aggregate": with_score},
        "without_docs": {"aggregate": without_score},
        "delta": with_score - without_score,
    }
    if agree is not None:
        item["with_docs"]["agreement_score"] = agree
    if hit is not None:
        item["retrieval_hit"] = hit
    return item


def _trust_scenarios() -> dict:
    healthy = [_ev(agree=0.8) for _ in range(20)]
    zeros = [_ev(with_score=0, hit=True) for _ in range(12)]
    misses = [_ev(with_score=0, hit=False, without_score=40) for _ in range(6)] + [_ev() for _ in range(6)]
    partial_hits = [_ev(hit=True) for _ in range(5)] + [_ev(hit=None) for _ in range(15)]
    no_hits = [_ev(hit=None) for _ in range(20)]
    noisy = [_ev(with_score=s, hit=True) for s in (5, 95, 10, 90, 15, 85, 20, 80, 25, 75, 30, 70)]
    cases = {
        "healthy_multirun": (healthy, None, [70.0, 71.0, 69.5]),
        "too_few": ([_ev() for _ in range(5)], None, None),
        "empty": ([], None, None),
        "zeros": (zeros, None, None),
        "misses_count_unusable": (misses, None, None),
        "partial_retrieval_coverage": (partial_hits, None, None),
        "no_retrieval_outcome": (no_hits, None, None),
        "noisy_scores": (noisy, None, None),
        "multirun_variance_fail": (healthy, None, [10.0, 60.0, 30.0]),
        "thresholds_override": (healthy, {"min_questions": 25}, None),
    }
    return {name: format_trust_block(evaluate_trust(evals, thresholds=thr, multirun_with_averages=multi))
            for name, (evals, thr, multi) in cases.items()}


# ── novelty: the as_dict JSON and the markdown block ─────────────────────────────────────────────────────────

def _artifact(**overrides) -> dict:
    artifact = {
        "artifact": {"ref": "skill:skills/accel-tuning", "kind": "skill"},
        "model": "m",
        "provider": "openai",
        "judge_model": "j",
        "atoms": {"n": 40, "n_probed": 40, "sampling": "all"},
        "paired": {
            "lenient": {"added": 0.42, "redundant": 0.5, "degraded": 0.0,
                        "still_missing": 0.08, "n_paired": 40, "ci95_added": [0.3, 0.55]},
            "strict": {"added": 0.30, "redundant": 0.6, "degraded": 0.0,
                       "still_missing": 0.10, "n_paired": 40, "ci95_added": [0.2, 0.4]},
        },
        "k0": {"lenient": 0.5, "strict": 0.6},
        "categories": {"harmful_conflicting": 0, "novel_useful": 30},
        "spans": {"n": 60, "tokens": 900, "frac_carrying_nothing": 0.12, "capped_atom_set": False},
        "shortening": [{"safe": True, "protection": 1.0, "value_coverage": 0.9,
                        "value_is_coverage_proxy": False}],
        "attribution": {"carrier_confirmation": {"n_atoms": 40, "n_located": 38,
                                                 "n_unlocated": 2, "frac_located": 0.95}},
        "controls": {"order_independent": False},
        "arms": {"lenient": {"no_artifact": 0.4, "full": 0.8}},
        "items": {"kind_agreement": {
            "no_artifact": {"arm": "no_artifact", "n_atoms_multi_kind": 40, "n_agree": 36,
                            "agreement": 0.9, "disagreeing": []},
            "full": {"arm": "full", "n_atoms_multi_kind": 40, "n_agree": 38,
                     "agreement": 0.95, "disagreeing": []},
        }},
        "bundle": {"include_resources": True, "n_files": 3, "entry_point_words": 400,
                   "resource_words_measured": 900, "resource_words_on_disk": 900,
                   "outside_share": 0.69, "excluded": [], "pointer_spans": {"n": 2, "tokens": 18}},
        "probes": {"n_probes": 110, "malformed_rate": 0.02,
                   "by_kind": {"paraphrase": {"generated": 40, "kept": 39, "rejected": 1,
                                              "malformed_rate": 0.025, "reasons": {"anchor_dropped": 1}}}},
        "warnings": [],
    }
    artifact.update(overrides)
    return artifact


def _novelty_scenarios() -> dict:
    stressed = _artifact(
        categories={"harmful_conflicting": 2, "novel_useful": 10},
        spans={"n": 60, "tokens": 900, "frac_carrying_nothing": 0.4, "capped_atom_set": True,
               "n_atoms_indexed": 300},
        shortening=[{"safe": True, "protection": 0.5, "value_coverage": 0.2,
                     "value_is_coverage_proxy": True}],
        attribution={"carrier_confirmation": {"n_atoms": 40, "n_located": 20, "n_unlocated": 20,
                                              "frac_located": 0.5}},
        controls={"order_independent": True},
        probes={"n_probes": 110, "malformed_rate": 0.4,
                "by_kind": {"paraphrase": {"generated": 40, "kept": 24, "rejected": 16,
                                           "malformed_rate": 0.4, "reasons": {"anchor_dropped": 16}}}},
        items={"kind_agreement": {"full": {"arm": "full", "n_atoms_multi_kind": 40, "n_agree": 20,
                                           "agreement": 0.5, "disagreeing": []}}},
    )
    cases = {
        "healthy": _artifact(),
        "stressed": stressed,
        "empty": {},
        "no_paired": _artifact(paired={}),
        "unclassified": _artifact(categories={}),
        "conflict_partial_coverage": _artifact(
            conflict_coverage={"pairs_compared": 5, "pairs_total": 50},
            categories={"harmful_conflicting": 0, "novel_useful": 30}),
        "conflict_found": _artifact(categories={"harmful_conflicting": 1, "novel_useful": 30}),
        "low_paired_added": _artifact(paired={"lenient": {"added": 0.05, "n_paired": 40}}),
        "unread_spans": _artifact(spans={"n": 60, "tokens": 900, "frac_carrying_nothing": 0.1,
                                         "n_unread": 5, "tokens_unread": 500, "capped_atom_set": False}),
        "bundle_entry_point_only": _artifact(bundle={"include_resources": False, "n_files": 1,
                                                     "entry_point_words": 400, "outside_share": 0.9}),
    }
    out = {}
    for name, artifact in cases.items():
        verdict = evaluate_novelty(artifact)
        out[name] = {
            "as_dict": verdict.as_dict(),
            "block": format_novelty_block([verdict]),
        }
    out["two_cells_block"] = format_novelty_block([evaluate_novelty(_artifact()),
                                                   evaluate_novelty(stressed)])
    return out


def _report_trust_scenarios() -> dict:
    """The ``trust`` block of the report JSON: its ``verdict`` carries the icon the markdown block shows."""
    from dokimi.report.generator import ReportGenerator

    cases = {
        "healthy": [_ev(agree=0.8) for _ in range(20)],
        "too_few": [_ev() for _ in range(5)],
        "no_retrieval_outcome": [_ev(hit=None) for _ in range(20)],
    }
    return {name: json.loads(ReportGenerator().generate_report(
                {"evaluations": evals}, {"questions": []}, output_format="json"))["trust"]
            for name, evals in cases.items()}


def _observed(base: Path) -> dict:
    return {
        "strict_runs": _strict_scenarios(base / "strict"),
        "no_document": _no_document(base),
        "band_errors": _band_errors(),
        "compare_regressions": _compare_scenarios(base / "compare"),
        "trust_blocks": _trust_scenarios(),
        "report_trust_json": _report_trust_scenarios(),
        "novelty": _novelty_scenarios(),
    }


def test_gate_outputs_match_golden(tmp_path):
    # Round-trip through JSON so tuples compare as the lists the golden file holds.
    observed = json.loads(json.dumps(_observed(tmp_path.resolve())))
    if os.environ.get("DOKIMI_UPDATE_GOLDEN"):
        GOLDEN.write_text(json.dumps(observed, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
                          encoding="utf-8")
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert sorted(observed) == sorted(golden), "gate golden sections changed; see the module docstring"
    for section in sorted(golden):
        assert observed[section] == golden[section], (
            f"gate golden section {section!r} moved; regenerate with DOKIMI_UPDATE_GOLDEN=1 "
            "only with a changelog fragment that says what moved")
