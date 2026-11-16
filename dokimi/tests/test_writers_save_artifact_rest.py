# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The JSON of `docs check`, `dashboard generate` and `baseline save` goes through save_artifact
(#229 P2-6): `schema_version` first, the run envelope on a run's output, listed by `runs list`, a
payload its schema refuses kept at `<name>.rejected` with exit 1, and the old readers still read it."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dokimi import __version__, artifacts, cli
from dokimi.artifacts import ENVELOPE_KEYS, load_artifact, save_artifact
from dokimi.commands import baseline as baseline_mod

RUN = "t37-run"
SPEC = ("weights:\n  coverage: 0.4\n  freshness_lite: 0.3\n  readability: 0.3\n"
        "metrics:\n  freshness_lite:\n    max_age_days: 365\n  readability:\n    grade_max: 12\n")
EVAL = {"library_name": "lib", "evaluated_at": "2026-11-01T00:00:00Z", "judge_model": "judge-m",
        "evaluations": [{"question_id": "q1", "question_text": "How do I run a loop?",
                         "with_docs": {"aggregate": 80.0, "correctness": 80, "grounded": True},
                         "without_docs": {"aggregate": 60.0, "correctness": 60}, "delta": 20.0}]}


@pytest.fixture(autouse=True)
def one_run(monkeypatch):
    monkeypatch.setenv(artifacts.RUN_ID_ENV, RUN)


def _cli(monkeypatch, *argv: str) -> int:
    monkeypatch.setattr("sys.argv", ["dokimi", *argv])
    try:
        cli.main()
    except SystemExit as exc:
        return int(exc.code or 0)
    return 0


def _saved(path: Path, kind: str) -> dict:
    """The file, after checking it leads with its schema_version and the envelope and loads as ``kind``."""
    data = json.loads(path.read_text(encoding="utf-8"))
    assert list(data)[:3] == ["schema_version", "run_id", "dokimi_version"]
    assert (data["schema_version"], data["run_id"], data["dokimi_version"]) == \
        (artifacts.SCHEMA_VERSIONS[kind], RUN, __version__)
    assert load_artifact(kind, path) == data
    return data


def _listed(monkeypatch, capsys, root: Path) -> dict[str, tuple[str, str | None]]:
    """`runs list --json` over ``root``: path -> (kind, run_id)."""
    capsys.readouterr()
    monkeypatch.setenv("DOKIMI_RUNS_DIR", str(root))
    assert _cli(monkeypatch, "runs", "list", "--json") == 0
    rows = json.loads(capsys.readouterr().out)["data"]["artifacts"]
    return {row["path"]: (row["kind"], row["run_id"]) for row in rows}


def _rejected(path: Path) -> dict:
    assert not path.exists()
    return json.loads(path.with_name(path.name + ".rejected").read_text(encoding="utf-8"))


# ── docs check ────────────────────────────────────────────────────────────────

def _check(monkeypatch, tmp_path: Path, out: Path) -> int:
    (tmp_path / "docs").mkdir(exist_ok=True)
    (tmp_path / "docs" / "a.md").write_text("# Install\n\nRun `pip install lib`.\n", encoding="utf-8")
    spec = tmp_path / "spec.yaml"
    spec.write_text(SPEC, encoding="utf-8")
    return _cli(monkeypatch, "docs", "check", "--root", str(tmp_path), "--spec", str(spec),
                "--out", str(out), "--out-md", str(tmp_path / "current.md"))


def test_the_docs_check_snapshot_is_a_docs_run_artifact(tmp_path, monkeypatch, capsys):
    out = tmp_path / "results" / "current.json"
    assert _check(monkeypatch, tmp_path, out) == 0

    data = _saved(out, "docs_run")
    assert (data["summary"]["docs"], [d["path"] for d in data["docs"]]) == (1, ["docs/a.md"])
    assert data["run_manifest"]["docs"]["count"] == 1
    assert _listed(monkeypatch, capsys, tmp_path / "results") == {"current.json": ("docs_run", RUN)}


def test_docs_compare_reads_a_saved_snapshot_and_one_from_before_the_envelope(tmp_path, monkeypatch, capsys):
    """An unstamped snapshot an older dokimi wrote is still a base `docs compare` reads."""
    cand = tmp_path / "cand.json"
    assert _check(monkeypatch, tmp_path, cand) == 0
    base = tmp_path / "base.json"
    old = {k: v for k, v in json.loads(cand.read_text(encoding="utf-8")).items()
           if k not in ("schema_version", *ENVELOPE_KEYS)}
    base.write_text(json.dumps({**old, "summary": {**old["summary"], "score": 0.0}}), encoding="utf-8")

    assert _cli(monkeypatch, "docs", "compare", "--base", str(base), "--candidate", str(cand),
                "--out", str(tmp_path / "cmp.json"), "--out-md", str(tmp_path / "cmp.md")) == 0
    cmp = json.loads((tmp_path / "cmp.json").read_text(encoding="utf-8"))
    assert cmp["diff"]["score"] == old["summary"]["score"] > 0


def test_a_snapshot_the_schema_refuses_is_kept_as_rejected_and_exits_1(tmp_path, monkeypatch, capsys):
    """A manifest time that is not text (a writer bug) is refused; the report is not written after it."""
    from dokimi.runner import run as run_mod

    build = run_mod.build_run_manifest
    monkeypatch.setattr(run_mod, "build_run_manifest", lambda **kw: {**build(**kw), "generated_at": None})
    out = tmp_path / "current.json"
    assert _check(monkeypatch, tmp_path, out) == 1

    assert _rejected(out)["run_manifest"]["generated_at"] is None
    assert not (tmp_path / "current.md").exists()
    err = capsys.readouterr().err
    assert err.startswith("Error: docs_run artifact rejected: ") and "run_manifest/generated_at" in err


# ── dashboard generate ────────────────────────────────────────────────────────

def _generate(monkeypatch, tmp_path: Path, out_dir: Path) -> int:
    eval_file = tmp_path / "results" / "lib" / "eval" / "lib.json"
    eval_file.parent.mkdir(parents=True, exist_ok=True)
    eval_file.write_text(json.dumps(EVAL), encoding="utf-8")
    return _cli(monkeypatch, "dashboard", "generate", "--results-dir", str(tmp_path / "results"),
                "--out-dir", str(out_dir))


def test_the_dashboard_json_is_a_dashboard_artifact(tmp_path, monkeypatch, capsys):
    out_dir = tmp_path / "results" / "dashboard"
    assert _generate(monkeypatch, tmp_path, out_dir) == 0

    data = _saved(out_dir / "dashboard.json", "dashboard")
    (product,) = data["products"]
    assert (product["product"], product["judge_model"], product["avg_delta"]) == ("lib", "judge-m", 20.0)
    # the judge marks a grounded question with `grounded: true` beside the scores (eval/judge.py)
    assert product["questions"][0]["with_docs_dimensions"] == {"correctness": 80, "grounded": True}
    # the hand-written eval opens with no schema_version, so only the dashboard is listed
    assert _listed(monkeypatch, capsys, tmp_path / "results") == {
        "dashboard/dashboard.json": ("dashboard", RUN)}


def test_a_dashboard_from_an_eval_with_untyped_fields_is_still_saved(tmp_path, monkeypatch):
    """A bad eval file is not a rejected save: the fields the dashboard copies keep the eval's value."""
    odd = {**EVAL, "evaluated_at": None, "judge_model": 3,
                "evaluations": [{**EVAL["evaluations"][0], "question_source": 7}]}
    eval_file = tmp_path / "results" / "eval" / "lib.json"
    eval_file.parent.mkdir(parents=True)
    eval_file.write_text(json.dumps(odd), encoding="utf-8")
    out_dir = tmp_path / "out"
    assert _cli(monkeypatch, "dashboard", "generate", "--results-dir", str(tmp_path / "results"),
                "--out-dir", str(out_dir)) == 0

    (product,) = _saved(out_dir / "dashboard.json", "dashboard")["products"]
    assert (product["evaluated_at"], product["judge_model"], product["questions"][0]["question_source"]) == \
        (None, 3, 7)


def test_a_dashboard_the_schema_refuses_is_kept_as_rejected_and_exits_1(tmp_path, monkeypatch, capsys):
    """A per-source count that is not a number (a writer bug) is refused; the markdown is written by then."""
    from dokimi.dashboard import aggregator

    by_source = aggregator._compute_by_source
    monkeypatch.setattr(aggregator, "_compute_by_source",
                        lambda qs: {k: {**v, "count": str(v["count"])} for k, v in by_source(qs).items()})
    out_dir = tmp_path / "out"
    assert _generate(monkeypatch, tmp_path, out_dir) == 1

    assert (out_dir / "DASHBOARD.md").exists()
    assert _rejected(out_dir / "dashboard.json")["products"][0]["by_source"]["persona"]["count"] == "1"
    err = capsys.readouterr().err
    assert err.startswith("Error: dashboard artifact rejected: ") and "by_source/persona/count" in err


# ── baseline save ─────────────────────────────────────────────────────────────

@pytest.fixture
def baselines(tmp_path, monkeypatch) -> Path:
    path = tmp_path / "results" / "baselines"
    monkeypatch.setattr(baseline_mod, "BASELINES_DIR", path)
    return path


def _eval(tmp_path: Path, monkeypatch) -> Path:
    """An eval artifact saved by another run, as `eval score pointwise` saves it."""
    monkeypatch.setenv(artifacts.RUN_ID_ENV, "eval-run")
    path = tmp_path / "eval" / "lib.json"
    save_artifact("eval", EVAL, path)
    monkeypatch.setenv(artifacts.RUN_ID_ENV, RUN)
    return path


def test_the_baselines_manifest_is_an_input_artifact_with_no_envelope(tmp_path, monkeypatch, capsys, baselines):
    """The manifest indexes every saved baseline, so no one run's id belongs on it."""
    assert _cli(monkeypatch, "baseline", "save", "--from-eval", str(_eval(tmp_path, monkeypatch)),
                "--name", "b1") == 0

    data = load_artifact("baseline_manifest", baselines / "manifest.json")
    assert list(data)[0] == "schema_version" and not set(ENVELOPE_KEYS) & set(data)
    (entry,) = data["baselines"]
    assert (entry["name"], entry["product"], entry["summary"]["avg_delta"]) == ("b1", "unknown", 20.0)
    assert _listed(monkeypatch, capsys, tmp_path / "results")["baselines/manifest.json"] == \
        ("baseline_manifest", None)


def test_the_saved_baseline_is_the_eval_byte_for_byte_with_its_own_envelope(tmp_path, monkeypatch, capsys,
                                                                            baselines):
    """Restamping the copy would credit the eval's scores to the `baseline save` run."""
    source = _eval(tmp_path, monkeypatch)
    assert _cli(monkeypatch, "baseline", "save", "--from-eval", str(source), "--name", "b1") == 0

    copy = baselines / "b1.json"
    assert copy.read_bytes() == source.read_bytes()
    assert load_artifact("eval", copy)["run_id"] == "eval-run"
    assert _listed(monkeypatch, capsys, tmp_path / "results")["baselines/b1.json"] == ("eval", "eval-run")


def test_baseline_list_and_compare_read_a_manifest_from_before_the_schema(tmp_path, monkeypatch, capsys,
                                                                         baselines):
    """A manifest an older dokimi wrote (no schema_version) is read, and the next save stamps it and keeps
    the keys it does not know."""
    source = _eval(tmp_path, monkeypatch)
    baselines.mkdir(parents=True)
    (baselines / "old.json").write_bytes(source.read_bytes())
    (baselines / "manifest.json").write_text(json.dumps({"note": "kept", "baselines": [
        {"name": "old", "product": "lib", "saved_at": "20261101T000000", "path": str(baselines / "old.json"),
         "summary": {"n": 1, "avg_with": 80.0, "avg_without": 60.0, "avg_delta": 20.0}, "tag": "v1"}]}),
        encoding="utf-8")

    assert _cli(monkeypatch, "baseline", "compare", "--eval", str(source), "--baseline", "old") == 0
    assert "No notable changes vs baseline (1 of 1 questions compared)" in capsys.readouterr().out
    assert _cli(monkeypatch, "baseline", "save", "--from-eval", str(source), "--name", "new") == 0
    assert _cli(monkeypatch, "baseline", "list") == 0
    listing = capsys.readouterr().out
    assert "old" in listing and "new" in listing
    manifest = load_artifact("baseline_manifest", baselines / "manifest.json")
    assert (manifest["note"], [b["name"] for b in manifest["baselines"]], manifest["baselines"][0]["tag"]) == \
        ("kept", ["old", "new"], "v1")


def test_a_manifest_the_schema_refuses_is_kept_as_rejected_and_exits_1(tmp_path, monkeypatch, capsys, baselines):
    """A summary that is not an object (a writer bug) is refused; the manifest on disk is untouched, and the copy
    is removed, so a retry under the same name saves."""
    source = _eval(tmp_path, monkeypatch)
    assert _cli(monkeypatch, "baseline", "save", "--from-eval", str(source), "--name", "b1") == 0
    before = (baselines / "manifest.json").read_bytes()
    monkeypatch.setattr(baseline_mod, "_compute_summary", lambda _data: [])
    assert _cli(monkeypatch, "baseline", "save", "--from-eval", str(source), "--name", "b2") == 1

    assert (baselines / "manifest.json").read_bytes() == before
    kept = json.loads((baselines / "manifest.json.rejected").read_text(encoding="utf-8"))
    assert kept["baselines"][-1]["summary"] == []
    assert capsys.readouterr().err.startswith("Error: baseline_manifest artifact rejected: ")
    assert not (baselines / "b2.json").exists()
    monkeypatch.undo()
    monkeypatch.setattr(baseline_mod, "BASELINES_DIR", baselines)
    assert _cli(monkeypatch, "baseline", "save", "--from-eval", str(source), "--name", "b2") == 0
    assert [b["name"] for b in load_artifact("baseline_manifest", baselines / "manifest.json")["baselines"]] == \
        ["b1", "b2"]


@pytest.mark.parametrize("entry", ['"saved_at": 123', '"summary": {"n": 0, "avg_delta": null}', '"product": ["a"]'],
                         ids=["saved_at", "summary", "product"])
def test_an_entry_baseline_list_reads_does_not_block_a_later_save(tmp_path, monkeypatch, capsys, baselines, entry):
    """The schema holds an entry to what the readers require, so a legacy entry they read is kept, as main kept it."""
    baselines.mkdir(parents=True)
    (baselines / "manifest.json").write_text('{"baselines": [{"name": "old", "path": "old.json", %s}]}' % entry,
                                             encoding="utf-8")
    assert _cli(monkeypatch, "baseline", "list") == 0
    assert _cli(monkeypatch, "baseline", "save", "--from-eval", str(_eval(tmp_path, monkeypatch)), "--name", "n1") == 0
    assert [b["name"] for b in load_artifact("baseline_manifest", baselines / "manifest.json")["baselines"]] == \
        ["old", "n1"]
