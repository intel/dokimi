# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Judge-panel repeat step: a retry keeps the saved repeats.

``do_grade`` skips a (key, judge) pair that already has a verdict, and the key of a
repeat does not change when its answer does. So rerunning ``repeat`` must not replace a
saved answer: its verdicts would then be kept for an answer that is no longer there.
"""

import importlib.util
import json
import sys
import types
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "research" / "judge-panel" / "judge_panel.py"


def _load(tmp_path, monkeypatch, calls, fail=(), seen=None, asked=None):
    llm = types.ModuleType("llm")
    llm.CACHE = None
    llm.LOG = None
    llm.NO_TEMPERATURE = set()

    def chat(model, messages, **kw):
        calls.append(kw["tag"])
        if seen is not None:
            seen.append({"model": model, "messages": messages, **kw})
        if kw["tag"] in fail:
            raise RuntimeError(f"timeout on {kw['tag']}")
        return {"text": f"new answer for {kw['tag']}"}

    def ask_json(judge, prompt, **kw):
        if asked is not None:
            asked.append((judge, kw["tag"]))
        return {"verdict": "correct", "reason": "ok"}

    def pmap(fn, items, workers=1, desc=""):
        out = []
        for it in items:
            try:
                out.append(fn(it))
            except Exception as exc:  # the real pmap returns the error, not raises
                out.append(exc)
        return out

    llm.chat = chat
    llm.ask_json = ask_json
    llm.pmap = pmap
    llm.stats = dict
    rp = types.ModuleType("run_probes")
    rp.SYSTEM = "system"
    rp.GRADE_PROMPT = ""
    rp.SCORE = {"correct": 1}
    monkeypatch.setitem(sys.modules, "llm", llm)
    monkeypatch.setitem(sys.modules, "run_probes", rp)
    monkeypatch.setenv("JP_STUDY", str(tmp_path / "study"))
    monkeypatch.setenv("JP_OUT", str(tmp_path / "out"))
    spec = importlib.util.spec_from_file_location("judge_panel_under_test", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_repeat_keeps_saved_answers_and_calls_only_missing_cells(tmp_path, monkeypatch):
    calls = []
    jp = _load(tmp_path, monkeypatch, calls)
    out = tmp_path / "out"
    base = [
        {"probe_id": "p1", "model": "m1", "arm": "no_skill", "question": "q1"},
        {"probe_id": "p2", "model": "m1", "arm": "no_skill", "question": "q2"},
    ]
    (out / "sample.jsonl").write_text("".join(json.dumps(r) + "\n" for r in base))
    saved = {"probe_id": "p1", "model": "m1", "arm": "no_skill_repeat", "answer": "saved", "question": "q1"}
    (out / "repeats.jsonl").write_text(json.dumps(saved) + "\n")

    jp.do_repeat(1)

    assert calls == ["repeat|m1|p2"]
    lines = (out / "repeats.jsonl").read_text().splitlines()
    rows = {r["probe_id"]: r for r in map(json.loads, lines)}
    assert rows["p1"]["answer"] == "saved"
    assert rows["p2"]["answer"] == "new answer for repeat|m1|p2"


def test_repeat_drops_saved_rows_for_cells_no_longer_sampled(tmp_path, monkeypatch):
    calls = []
    jp = _load(tmp_path, monkeypatch, calls)
    out = tmp_path / "out"
    base = [{"probe_id": "p1", "model": "m1", "arm": "no_skill", "question": "q1"}]
    (out / "sample.jsonl").write_text(json.dumps(base[0]) + "\n")
    stale = {"probe_id": "p9", "model": "m1", "arm": "no_skill_repeat", "answer": "old", "question": "q9"}
    (out / "repeats.jsonl").write_text(json.dumps(stale) + "\n")

    jp.do_repeat(1)

    assert calls == ["repeat|m1|p1"]
    rows = [json.loads(l) for l in (out / "repeats.jsonl").read_text().splitlines()]
    assert [r["probe_id"] for r in rows] == ["p1"]


def test_repeat_without_saved_file_runs_every_cell(tmp_path, monkeypatch):
    calls = []
    jp = _load(tmp_path, monkeypatch, calls)
    out = tmp_path / "out"
    base = [{"probe_id": "p1", "model": "m1", "arm": "no_skill", "question": "q1"}]
    (out / "sample.jsonl").write_text(json.dumps(base[0]) + "\n")

    jp.do_repeat(1)

    assert calls == ["repeat|m1|p1"]
    assert len((out / "repeats.jsonl").read_text().splitlines()) == 1


def test_repeat_drops_grades_of_dropped_repeats(tmp_path, monkeypatch):
    calls = []
    jp = _load(tmp_path, monkeypatch, calls)
    out = tmp_path / "out"
    base = [{"probe_id": "p1", "model": "m1", "arm": "no_skill", "question": "q1"}]
    (out / "sample.jsonl").write_text(json.dumps(base[0]) + "\n")
    kept = {"probe_id": "p1", "model": "m1", "arm": "no_skill_repeat", "answer": "saved", "question": "q1"}
    stale = {"probe_id": "p9", "model": "m1", "arm": "no_skill_repeat", "answer": "old", "question": "q9"}
    (out / "repeats.jsonl").write_text(json.dumps(kept) + "\n" + json.dumps(stale) + "\n")
    grades = [
        {"key": "p1|no_skill_repeat|m1", "judge": "j1", "verdict": "correct_complete"},
        {"key": "p9|no_skill_repeat|m1", "judge": "j1", "verdict": "wrong_other"},
    ]
    (out / "grades.jsonl").write_text("".join(json.dumps(g) + "\n" for g in grades))

    jp.do_repeat(1)

    assert calls == []
    left = [json.loads(l)["key"] for l in (out / "grades.jsonl").read_text().splitlines()]
    assert left == ["p1|no_skill_repeat|m1"]


def test_repeat_asks_for_a_fresh_greedy_answer_with_the_system_prompt(tmp_path, monkeypatch):
    # The noise floor is measured against a fresh answer: no cache, temperature 0, the study's
    # system prompt and token cap, and the repeat arm name. Each of these is pinned.
    seen = []
    jp = _load(tmp_path, monkeypatch, [], seen=seen)
    out = tmp_path / "out"
    base = [{"probe_id": "p1", "model": "m1", "arm": "no_skill", "question": "q1"}]
    (out / "sample.jsonl").write_text(json.dumps(base[0]) + "\n")

    jp.do_repeat(1)

    (call,) = seen
    assert call["use_cache"] is False
    assert call["temperature"] == 0.0
    assert call["max_tokens"] == 1400
    assert call["messages"] == [{"role": "system", "content": "system"}, {"role": "user", "content": "q1"}]
    (row,) = [json.loads(l) for l in (out / "repeats.jsonl").read_text().splitlines()]
    assert row["arm"] == "no_skill_repeat"


def test_repeat_retries_a_failed_cell_and_does_not_save_it(tmp_path, monkeypatch):
    out = tmp_path / "out"
    out.mkdir()
    base = [
        {"probe_id": "p1", "model": "m1", "arm": "no_skill", "question": "q1"},
        {"probe_id": "p2", "model": "m1", "arm": "no_skill", "question": "q2"},
    ]
    (out / "sample.jsonl").write_text("".join(json.dumps(r) + "\n" for r in base))

    jp = _load(tmp_path, monkeypatch, [], fail={"repeat|m1|p2"})
    jp.do_repeat(1)
    saved = [json.loads(l)["probe_id"] for l in (out / "repeats.jsonl").read_text().splitlines()]
    assert saved == ["p1"]

    calls = []
    jp = _load(tmp_path, monkeypatch, calls)
    jp.do_repeat(1)
    assert calls == ["repeat|m1|p2"]


def test_purge_drops_only_the_grades_of_the_dropped_cell(tmp_path, monkeypatch):
    # Two cells share a probe; only the one no longer sampled loses its grades.
    calls = []
    jp = _load(tmp_path, monkeypatch, calls)
    out = tmp_path / "out"
    (out / "sample.jsonl").write_text(
        json.dumps({"probe_id": "p1", "model": "m1", "arm": "no_skill", "question": "q1"}) + "\n")
    kept = {"probe_id": "p1", "model": "m1", "arm": "no_skill_repeat", "answer": "a", "question": "q1"}
    stale = {"probe_id": "p1", "model": "m2", "arm": "no_skill_repeat", "answer": "b", "question": "q1"}
    (out / "repeats.jsonl").write_text(json.dumps(kept) + "\n" + json.dumps(stale) + "\n")
    grades = [
        {"key": "p1|no_skill_repeat|m1", "judge": "j1", "verdict": "correct_complete"},
        {"key": "p1|no_skill_repeat|m2", "judge": "j1", "verdict": "wrong_other"},
    ]
    (out / "grades.jsonl").write_text("".join(json.dumps(g) + "\n" for g in grades))

    jp.do_repeat(1)

    assert calls == []
    left = [json.loads(l)["key"] for l in (out / "grades.jsonl").read_text().splitlines()]
    assert left == ["p1|no_skill_repeat|m1"]


def test_grade_asks_only_for_rows_without_a_grade(tmp_path, monkeypatch):
    # A repeat whose grades were purged has none, so grade must ask for it again; a row that
    # still has its grade must not be asked again.
    study = tmp_path / "study" / "data"
    study.mkdir(parents=True)
    (study / "probes.jsonl").write_text(json.dumps({
        "probe_id": "p1", "atom_statement": "s", "atom_qualifiers": [], "expected_core": "c"}) + "\n")
    out = tmp_path / "out"
    out.mkdir()
    (out / "sample.jsonl").write_text(json.dumps({
        "probe_id": "p1", "model": "m1", "arm": "no_skill", "question": "q1", "answer": "x",
        "adversarial": False, "style": "plain"}) + "\n")
    (out / "repeats.jsonl").write_text(json.dumps({
        "probe_id": "p1", "model": "m1", "arm": "no_skill_repeat", "answer": "y", "question": "q1",
        "adversarial": False, "style": "plain"}) + "\n")
    # The sampled row keeps its grade; only the purged repeat must be asked again.
    (out / "grades.jsonl").write_text(json.dumps(
        {"key": "p1|no_skill|m1", "judge": "j1", "verdict": "correct_complete"}) + "\n")
    asked = []
    jp = _load(tmp_path, monkeypatch, [], asked=asked)

    jp.do_grade(1, ["j1"])

    assert asked == [("j1", "grade|j1|p1|no_skill_repeat|m1")]


def test_resample_drops_grades_of_answers_that_changed(tmp_path, monkeypatch):
    # The study is re-run: p1's answer changes under the same key, so its grade must go.
    study = tmp_path / "study" / "data"
    study.mkdir(parents=True)
    arms = ["no_skill", "root", "routed", "full", "placebo", "shuffled"]

    def results(p1_answer):
        return "".join(json.dumps({
            "probe_id": p, "model": "claude-opus-5", "arm": a, "skill": "s", "adversarial": False,
            "verdict": "correct_complete", "answer": p1_answer if p == "p1" else "same"}) + "\n"
            for p in ("p1", "p2") for a in arms)

    (study / "probe_results.jsonl").write_text(results("old"))
    jp = _load(tmp_path, monkeypatch, [])
    jp.do_sample(2)
    grades = tmp_path / "out" / "grades.jsonl"
    grades.write_text("".join(json.dumps({"key": f"{p}|no_skill|claude-opus-5", "judge": "j1",
                                          "verdict": "correct_complete"}) + "\n" for p in ("p1", "p2")))
    (study / "probe_results.jsonl").write_text(results("new"))

    jp.do_sample(2)

    left = [json.loads(line)["key"] for line in grades.read_text().splitlines()]
    assert left == ["p2|no_skill|claude-opus-5"]
    # A key that leaves the sample loses its grade too, or it would return with a new answer.
    (study / "probe_results.jsonl").write_text(results("new").replace('"p2"', '"p3"'))
    jp.do_sample(2)
    assert grades.read_text() == ""


def test_resample_drops_repeats_of_cells_that_left_and_their_grades(tmp_path, monkeypatch):
    # p9 leaves the sample: its repeat and the repeat's grade go, or do_grade would grade a repeat
    # with no baseline and gen.py would stop with a KeyError.
    study = tmp_path / "study" / "data"
    study.mkdir(parents=True)
    arms = ["no_skill", "root", "routed", "full", "placebo", "shuffled"]
    (study / "probe_results.jsonl").write_text("".join(json.dumps({
        "probe_id": "p1", "model": "claude-opus-5", "arm": a, "skill": "s", "adversarial": False,
        "verdict": "correct_complete", "answer": "same"}) + "\n" for a in arms))
    jp = _load(tmp_path, monkeypatch, [])
    out = tmp_path / "out"
    repeats = [{"probe_id": p, "model": "claude-opus-5", "arm": "no_skill_repeat", "answer": "r",
                "question": "q"} for p in ("p1", "p9")]
    (out / "repeats.jsonl").write_text("".join(json.dumps(r) + "\n" for r in repeats))
    grades = [{"key": f"{p}|no_skill_repeat|claude-opus-5", "judge": "j1", "verdict": "wrong_other"}
              for p in ("p1", "p9")]
    (out / "grades.jsonl").write_text("".join(json.dumps(g) + "\n" for g in grades))

    jp.do_sample(2)

    kept = [json.loads(line)["probe_id"] for line in (out / "repeats.jsonl").read_text().splitlines()]
    assert kept == ["p1"]
    left = [json.loads(line)["key"] for line in (out / "grades.jsonl").read_text().splitlines()]
    assert left == ["p1|no_skill_repeat|claude-opus-5"]
