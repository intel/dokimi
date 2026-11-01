# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Leak audit: markers in what an agent is sent, applied to the skill-catalog and docker-claude paths."""

from __future__ import annotations

from pathlib import Path

import dokimi.eval.agent_runner as ar_mod
from dokimi.eval.agent_runner import run_agent_loop
from dokimi.harnesses import load_task
from dokimi.harnesses.agent_sandbox import AgentWorkspace
from dokimi.harnesses.docker_solver import _CONTAINER_PLUGIN, DockerSolverHarness
from dokimi.harnesses.payload_audit import added_framing, audit_payload, markers_for, new_leaks
from dokimi.harnesses.skill_treatment import TREATMENT_NOTICE, Treatment
from dokimi.skills import load_skill
from dokimi.treatments.arms import SkillCatalogTreatment


def _skill(root: Path, name: str, description: str, body: str) -> Path:
    d = root / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {description}\n---\n{body}\n",
                                encoding="utf-8")
    return d / "SKILL.md"


def test_planted_markers_are_found_with_their_path():
    payload = {"messages": [{"role": "user", "content": "Q-17: how do I sum?"}],
               "meta": {"expected_skill": "x"}, "cwd": "/srv/dokimi-run-ab12/work"}
    leaks = audit_payload(payload, markers_for(question_ids=["Q-17"]))
    found = {(lk.marker, lk.path) for lk in leaks}
    assert ("Q-17", "$.messages[0].content") in found
    assert ("expected_skill", "$.meta.expected_skill#key") in found
    # A hyphenated path still names the tool: `dokimi-run-` is a leak.
    assert ("dokimi", "$.cwd") in found


def test_marker_inside_a_longer_word_is_not_a_leak():
    assert audit_payload({"t": "evaluate the baseline_x reevaluation"}, ["eval"]) == []
    assert [lk.marker for lk in audit_payload({"t": "see eval_queries.json"}, ["eval"])] == ["eval"]


def test_markers_match_any_case_only_between_non_alphanumerics_and_inside_tuples():
    assert [lk.marker for lk in audit_payload({"t": "Run by DOKIMI"}, ["dokimi"])] == ["dokimi"]
    assert audit_payload({"t": "xdokimi and dokimix"}, ["dokimi"]) == []
    assert [(lk.path, lk.marker) for lk in audit_payload({"t": ("ok", "the judge")}, ["judge"])] == [
        ("$.t[1]", "judge")]


def test_allowed_texts_blank_longest_first():
    # Shortest first would blank "skill" inside the longer allowance, leaving "eval" visible.
    assert audit_payload({"t": "eval the skill"}, ["eval"], allowed=["skill", "eval the skill"]) == []


def test_operator_content_is_blanked_but_dokimi_text_around_it_is_not():
    desc = "Compare against a baseline run before tuning."
    payload = {"tools": [{"description": f"Load skill. {desc}"}],
               "system": "You are in the treatment arm."}
    leaks = audit_payload(payload, allowed=[desc])
    assert [(lk.marker, lk.path) for lk in leaks] == [("treatment", "$.system")]
    # Without the allowance the skill's own word is reported too.
    assert {lk.marker for lk in audit_payload(payload)} == {"baseline", "treatment"}


def test_new_leaks_ignore_what_the_stock_harness_already_says():
    reference = {"system": "Test the golden path and judge edge cases."}
    measured = {"system": "Test the golden path and judge edge cases.", "user": "graded by the judge"}
    assert [(lk.path, lk.marker) for lk in new_leaks(measured, reference)] == [("$.user", "judge")]


def test_skill_catalog_request_carries_no_dokimi_artifacts(tmp_path, monkeypatch):
    # The request the skill-catalog arm actually sends (messages + tool schemas), captured
    # at the LLM chokepoint, holds the query and the skills' own text and nothing else of ours.
    paths = [_skill(tmp_path / "cat", "sum-fast", "Use when summing arrays; beats the baseline loop.",
                    "# Sum\nUse a reduction."),
             _skill(tmp_path / "cat", "io-read", "Use when reading files.", "# IO\nRead in chunks.")]
    skills = [load_skill(p) for p in paths]
    arm = SkillCatalogTreatment(skills)
    question = "How do I sum a big array quickly?"
    cfg = arm.prepare(question, "lib")
    sent = []

    class _Msg:
        content, tool_calls = "Use a reduction.", None

    class _Resp:
        choices = [type("C", (), {"message": _Msg()})()]
        usage = type("U", (), {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2})()

    def fake(messages, model, tools=None, **kw):
        sent.append({"messages": [dict(m) for m in messages], "tools": tools})
        return _Resp()

    monkeypatch.setattr(ar_mod, "chat_completion", fake)
    run_agent_loop(question, cfg.tools, model="m", system_prompt=cfg.system_prompt)
    allowed = [question] + [s.description for s in skills] + [s.name for s in skills]
    markers = markers_for(question_ids=["q-001"], arm_names=[arm.name], paths=[str(tmp_path)])
    assert sent and audit_payload(sent, markers, allowed) == []
    # The audit is live on this payload: the arm's own name, planted, is caught.
    sent[0]["messages"].append({"role": "user", "content": f"arm {arm.name}"})
    assert {lk.marker for lk in audit_payload(sent, markers, allowed)} == {"skill_catalog", arm.name}


def _task(tmp_path: Path) -> Path:
    task = tmp_path / "content" / "tasks" / "demo-task"
    (task / "tests").mkdir(parents=True)
    (task / "solution").mkdir()
    (task / "environment").mkdir()
    (task / "instruction.md").write_text("Make sum() faster.", encoding="utf-8")
    (task / "task.toml").write_text("[environment]\n", encoding="utf-8")
    return task


def test_docker_claude_discover_prompt_is_task_framing_only(tmp_path):
    task = load_task(_task(tmp_path))
    treatment = Treatment(name="sum-fast", plugin="sum-fast")
    out = tmp_path / "out"
    out.mkdir()
    prompts = {}
    for notice in (True, False):
        h = DockerSolverHarness(harness_id="docker-claude", mode="claude", skill_notice=notice)
        argv = h._claude_args(task, treatment, "/plugins/sum-fast", "haiku", out)
        prompt = argv[argv.index("-p") + 1]
        prompts[notice] = added_framing(prompt, task.instruction)
        assert audit_payload(prompt, markers_for(paths=[str(task.path)])) == []
    # Discover adds nothing to the task instruction; the skill arm adds only the notice.
    assert prompts[False] == ""
    assert prompts[True].startswith(TREATMENT_NOTICE.format(skill_id=treatment.skill_id).strip())


def test_docker_claude_arms_send_no_harness_artifacts(tmp_path):
    # The whole argv (plugin dir, tools, prompt) and the agent's cwd, as the solver sees them.
    task = load_task(_task(tmp_path))
    treatment = Treatment(name="sum-fast", plugin="sum-fast")
    out = tmp_path / "out"
    out.mkdir()
    markers = markers_for(paths=[str(task.path), str(tmp_path)])
    allowed = [task.instruction, TREATMENT_NOTICE.format(skill_id=treatment.skill_id)]
    for notice in (True, False):
        h = DockerSolverHarness(harness_id="docker-claude", mode="claude", skill_notice=notice)
        argv = h._claude_args(task, treatment, _CONTAINER_PLUGIN, "haiku", out)
        assert _CONTAINER_PLUGIN in argv and "abskill" not in " ".join(argv)
        assert audit_payload(argv, markers, allowed=allowed) == []
    ws = AgentWorkspace.create()
    try:
        assert audit_payload({"cwd": str(ws.work), "root": ws.root.name}, markers) == []
    finally:
        ws.cleanup()
