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


def _load(tmp_path, monkeypatch, calls):
    llm = types.ModuleType("llm")
    llm.CACHE = None
    llm.LOG = None
    llm.NO_TEMPERATURE = set()

    def chat(model, messages, **kw):
        calls.append(kw["tag"])
        return {"text": f"new answer for {kw['tag']}"}

    def pmap(fn, items, workers=1, desc=""):
        out = []
        for it in items:
            try:
                out.append(fn(it))
            except Exception as exc:  # the real pmap returns the error, not raises
                out.append(exc)
        return out

    llm.chat = chat
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


def test_repeat_without_saved_file_runs_every_cell(tmp_path, monkeypatch):
    calls = []
    jp = _load(tmp_path, monkeypatch, calls)
    out = tmp_path / "out"
    base = [{"probe_id": "p1", "model": "m1", "arm": "no_skill", "question": "q1"}]
    (out / "sample.jsonl").write_text(json.dumps(base[0]) + "\n")

    jp.do_repeat(1)

    assert calls == ["repeat|m1|p1"]
    assert len((out / "repeats.jsonl").read_text().splitlines()) == 1
