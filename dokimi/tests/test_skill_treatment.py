# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Tests for how the with-skill arm installs, announces and measures its skill."""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from dokimi.harnesses.claude_json import _parse_claude_json, result_event, solver_error
from dokimi.harnesses.skill_treatment import (
    Treatment,
    build_plugin,
    check_skill,
    plugin_slug,
    stream_stats,
    treatment_notice,
)
from dokimi.harnesses.solver_stream import record_solver, record_stream, run_solver
from dokimi.process import FakeRunner, reply


def _skill(root, name="onetbb-quickstart", dirname=None, extra=""):
    d = root / (dirname or name)
    (d / "references").mkdir(parents=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: Use for {name} things.\n{extra}---\n# Body\n",
        encoding="utf-8")
    (d / "references" / "api.md").write_text("ref", encoding="utf-8")
    return d


def _ev(**kw):
    return json.dumps(kw)


def _init(*skills, plugins=None, **kw):
    if plugins is not None:
        kw["plugins"] = [{"name": p, "path": f"/opt/{p}"} for p in plugins]
    return _ev(type="system", subtype="init", skills=list(skills), **kw)


def _call(tool, use_id, **inp):
    return _ev(type="assistant", message={"content": [
        {"type": "tool_use", "id": use_id, "name": tool, "input": inp}]})


def _result_of(use_id, error=False, command=None):
    extra = {"tool_use_result": {"success": True, "commandName": command}} if command else {}
    return _ev(type="user", message={"content": [
        {"type": "tool_result", "tool_use_id": use_id, "is_error": error, "content": "x"}]},
        **extra)


RESULT = _ev(type="result", num_turns=2, total_cost_usd=0.01, usage={"output_tokens": 5})
TBB = Treatment(name="onetbb-quickstart", plugin="onetbb-quickstart")


# ── building the plugin ───────────────────────────────────────────────────────


def test_build_plugin_wraps_the_whole_skill_under_its_frontmatter_name(tmp_path):
    skill = _skill(tmp_path, name="onetbb-quickstart", dirname="checkout-dir")
    dest = tmp_path / "plugin"
    treatment = build_plugin(skill, dest)
    assert treatment == TBB
    manifest = json.loads((dest / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    assert manifest["name"] == "onetbb-quickstart"
    assert (dest / "skills" / "onetbb-quickstart" / "SKILL.md").is_file()
    assert (dest / "skills" / "onetbb-quickstart" / "references" / "api.md").is_file()


@pytest.mark.parametrize("name, slug", [
    ("My TBB Skill.v2", "my-tbb-skill-v2"),   # spaces and dots stop the plugin loading
    ("../../../escaped", "escaped"),          # cannot leave the destination
    ("foo/bar", "foo-bar"),
    ("OneTBB_quick", "onetbb_quick"),
    ("...", "skill"),
])
def test_plugin_name_is_a_safe_slug(tmp_path, name, slug):
    assert plugin_slug(name) == slug
    dest = tmp_path / "plugin"
    treatment = build_plugin(_skill(tmp_path, name="placeholder", dirname="s"), dest)
    assert treatment.plugin == "placeholder"
    # The copy always lands directly under <dest>/skills.
    escaped = build_plugin(_skill(tmp_path / "x", name=name.replace("/", "_"), dirname="s"),
                           tmp_path / "p2")
    assert (tmp_path / "p2" / "skills" / escaped.plugin / "SKILL.md").is_file()


def test_build_plugin_leaves_the_skills_evals_out(tmp_path):
    """A skill's evals/ holds prompts and expected outputs: an answer key, not the skill."""
    skill = _skill(tmp_path, name="onetbb-quickstart", dirname="checkout-dir")
    (skill / "evals").mkdir()
    (skill / "evals" / "evals.json").write_text('{"expected": "the fix"}', encoding="utf-8")
    (skill / "references" / "evals").mkdir()
    (skill / "references" / "evals" / "note.md").write_text("kept", encoding="utf-8")
    dest = tmp_path / "plugin"
    build_plugin(skill, dest)
    copied = dest / "skills" / "onetbb-quickstart"
    assert not (copied / "evals").exists()
    assert (copied / "references" / "evals" / "note.md").is_file()  # only the top level


def test_build_plugin_refuses_a_directory_without_a_loadable_skill(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(ValueError, match="no SKILL.md"):
        build_plugin(empty, tmp_path / "p")
    bad = tmp_path / "bad"
    bad.mkdir()
    (bad / "SKILL.md").write_text("---\nname: [unterminated\n---\n", encoding="utf-8")
    with pytest.raises(ValueError):
        build_plugin(bad, tmp_path / "p2")


def test_build_plugin_follows_links_inside_the_skill(tmp_path):
    skill = _skill(tmp_path)
    (skill / "shared").mkdir()
    (skill / "shared" / "a.md").write_text("shared content", encoding="utf-8")
    (skill / "references" / "linked.md").symlink_to("../shared/a.md")
    (skill / "references" / "all").symlink_to("../shared", target_is_directory=True)
    dest = tmp_path / "plugin"
    build_plugin(skill, dest)
    copied = dest / "skills" / "onetbb-quickstart" / "references"
    assert not (copied / "linked.md").is_symlink() and not (copied / "all").is_symlink()
    assert (copied / "linked.md").read_text(encoding="utf-8") == "shared content"
    assert (copied / "all" / "a.md").read_text(encoding="utf-8") == "shared content"


def _harbor_checkout(tmp_path):
    """A content checkout: skills/ next to a task with its grader files."""
    task = tmp_path / "content" / "evaluation" / "harbor" / "tasks" / "onetbb-histogram"
    for sub in ("solution", "tests"):
        (task / sub).mkdir(parents=True)
        (task / sub / "answer.cpp").write_text("the answer", encoding="utf-8")
    (task / "task.toml").write_text("", encoding="utf-8")
    return _skill(tmp_path / "content" / "skills", name="demo-out")


@pytest.mark.parametrize("target, why", [
    ("../../../evaluation/harbor/tasks/onetbb-histogram", "leaves the skill"),  # grader files
    ("../../..", "leaves the skill"),                                          # the checkout
    ("..", "own ancestor"),                                                    # a loop inside
    ("../evals", "leaves the skill"),                                          # the answer key
    ("missing", "does not resolve"),
])
def test_build_plugin_refuses_a_link_out_of_the_skill(tmp_path, target, why):
    skill = _harbor_checkout(tmp_path)
    (skill / "evals").mkdir()
    (skill / "evals" / "evals.json").write_text("{}", encoding="utf-8")
    (skill / "references" / "shared").symlink_to(target)
    dest = tmp_path / "plugin"
    with pytest.raises(ValueError, match=why):
        build_plugin(skill, dest)
    assert not dest.exists()  # refused before anything is copied (no ELOOP-deep tree)


def test_a_link_named_evals_at_the_top_is_left_out_not_refused(tmp_path):
    skill = _harbor_checkout(tmp_path)
    (skill / "evals").symlink_to("../..")  # never copied, so never followed
    build_plugin(skill, tmp_path / "plugin")
    assert not (tmp_path / "plugin" / "skills" / "demo-out" / "evals").exists()


@pytest.mark.parametrize("extra, why", [
    ("disable-model-invocation: true\n", "disable-model-invocation"),
    ("context: fork\n", "context: fork"),
    ("model: haiku\n", "model: haiku"),
    ("hooks:\n  PreToolUse:\n    - hooks:\n        - type: command\n          command: x\n", "hooks"),
])
def test_frontmatter_that_changes_more_than_the_guidance_is_refused(tmp_path, extra, why):
    skill = _skill(tmp_path, extra=extra)
    with pytest.raises(ValueError, match=why):
        check_skill(skill)
    with pytest.raises(ValueError, match="part of the treatment"):
        build_plugin(skill, tmp_path / "plugin")


@pytest.mark.parametrize("value, refused", [
    ("1", True), ("yes", True), ('"yes"', True), ("' On '", True), ('"TRUE"', True),
    ("true", True), ("false", False), ("0", False), ("'no'", False), ("''", False),
])
def test_disable_model_invocation_is_read_as_the_cli_reads_a_boolean(tmp_path, value, refused):
    skill = _skill(tmp_path, extra=f"disable-model-invocation: {value}\n")
    if refused:
        with pytest.raises(ValueError, match="disable-model-invocation"):
            check_skill(skill)
    else:
        assert check_skill(skill) == "onetbb-quickstart"


def _dir_link(link, target):
    """A directory link; a junction on Windows without the symlink privilege."""
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        if sys.platform != "win32":
            pytest.skip("cannot create links here")
        subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(link.parent / target)],
                       check=True, capture_output=True)


def test_two_directory_links_that_loop_through_each_other_are_refused(tmp_path):
    skill = _skill(tmp_path)  # each link passes the ancestor rule alone: a/b -> c, c/d -> a
    (skill / "a").mkdir()
    (skill / "c").mkdir()
    _dir_link(skill / "a" / "b", "../c")
    _dir_link(skill / "c" / "d", "../a")
    with pytest.raises(ValueError, match="loops"):
        check_skill(skill)


def test_links_to_one_directory_from_many_places_are_not_a_loop(tmp_path):
    skill = _skill(tmp_path)
    for name in ("x", "y"):
        (skill / name).mkdir()
        _dir_link(skill / name / "refs", "../references")
    assert check_skill(skill) == "onetbb-quickstart"


def test_an_unreadable_subdirectory_is_not_a_loop_error(tmp_path, monkeypatch):
    skill = _skill(tmp_path)
    (skill / "locked").mkdir()
    real = os.scandir

    def scandir(path):
        if str(path).endswith("locked"):
            raise PermissionError(13, "denied", str(path))
        return real(path)

    monkeypatch.setattr("dokimi.harnesses.skill_treatment.os.scandir", scandir)
    assert check_skill(skill) == "onetbb-quickstart"


def test_a_skill_only_the_model_can_invoke_is_a_valid_treatment(tmp_path):
    assert check_skill(_skill(tmp_path, extra="user-invocable: false\n")) == "onetbb-quickstart"


def test_notice_names_the_skill_and_nothing_about_the_task():
    text = treatment_notice(Treatment(name="My DPNP.v2", plugin="my-dpnp-v2"))
    # The id the CLI lists, not the frontmatter name: the bare slug is rejected.
    assert "`my-dpnp-v2:my-dpnp-v2`" in text and "Skill tool" in text
    assert "trust the environment" in text
    assert treatment_notice(None) == ""


# ── reading the stream ────────────────────────────────────────────────────────


def test_stream_stats_counts_tools_offered_skills_and_successful_treatment_calls():
    stream = "\n".join([
        _init("onetbb-quickstart:onetbb-quickstart", "doctor"),
        _call("Skill", "a", skill="onetbb-quickstart:onetbb-quickstart"), _result_of("a"),
        _call("Bash", "b"), "not json", _call("Bash", "c"),
        RESULT,
    ])
    stats = stream_stats(stream, TBB)
    assert stats["tool_calls"] == {"Skill": 1, "Bash": 2}
    assert stats["offered_skills"] == ["onetbb-quickstart:onetbb-quickstart", "doctor"]
    assert stats["n_skill_invocations"] == 1


def test_a_rejected_or_foreign_skill_call_is_not_evidence_of_reach():
    stream = "\n".join([
        _init("doctor"),
        _call("Skill", "a", skill="onetbb-quickstart"), _result_of("a", error=True),
        _call("Skill", "b", skill="doctor"), _result_of("b"),
        RESULT,
    ])
    stats = stream_stats(stream, TBB)
    assert stats["tool_calls"] == {"Skill": 2}
    assert stats["n_skill_invocations"] == 0


def test_baseline_has_no_treatment_and_no_invocation_count():
    """A baseline cannot invoke a treatment: no key, so a rate over it is null, not 0%."""
    stats = stream_stats("\n".join([_init("doctor"), _call("Skill", "a", skill="doctor"), RESULT]))
    assert "n_skill_invocations" not in stats and stats["offered_skills"] == ["doctor"]


MY = Treatment(name="My TBB Skill.v2", plugin="my-tbb-skill-v2")


@pytest.mark.parametrize("requested, error, command, counted", [
    ("My TBB Skill.v2", False, None, 1),                        # the frontmatter name
    ("/my-tbb-skill-v2:my-tbb-skill-v2", False, None, 1),       # leading / stripped
    ("my-tbb-skill-v2", True, None, 0),                         # bare slug: CLI rejects it
    ("something-else", False, "my-tbb-skill-v2:my-tbb-skill-v2", 1),  # resolved by the CLI
    ("my-tbb-skill-v2:my-tbb-skill-v2", True, None, 0),
])
def test_a_treatment_call_counts_by_any_id_the_cli_accepts(requested, error, command, counted):
    stream = "\n".join([_init("my-tbb-skill-v2:my-tbb-skill-v2", plugins=["my-tbb-skill-v2"]),
                        _call("Skill", "a", skill=requested),
                        _result_of("a", error=error, command=command), RESULT])
    assert stream_stats(stream, MY)["n_skill_invocations"] == counted


def test_a_bare_plugin_id_that_is_a_core_command_is_not_the_treatment():
    doctor = Treatment(name="Doctor", plugin="doctor")
    stream = "\n".join([_init("doctor", plugins=[]), _call("Skill", "a", skill="doctor"),
                        _result_of("a"), RESULT])
    assert stream_stats(stream, doctor)["n_skill_invocations"] == 0


def test_stream_stats_records_plugins_errors_and_unexpected_skills():
    stream = "\n".join([
        _init("onetbb-quickstart:onetbb-quickstart", "doctor", "design", "policy-skill",
              plugins=["onetbb-quickstart"], plugin_errors=[{"plugin": "x", "error": "bad"}]),
        RESULT])
    stats = stream_stats(stream, TBB)
    assert stats["offered_plugins"] == ["onetbb-quickstart"]
    assert stats["plugin_errors"] == [{"plugin": "x", "error": "bad"}]
    assert stats["unexpected_skills"] == ["policy-skill"]
    assert "unexpected_skills" not in stream_stats(_init("doctor"), None)


def test_stream_stats_skips_events_of_an_unexpected_shape():
    stream = "\n".join([
        _ev(type="assistant", message="text"),
        _ev(type="assistant", message={"content": [
            {"type": "tool_use", "id": "a", "name": "Skill", "input": "onetbb-quickstart"}]}),
        _ev(type="user", message={"content": "x"}, tool_use_result="ok"),
        _ev(type="system", subtype="init", skills="doctor", plugins=["onetbb-quickstart"]),
        RESULT])
    stats = stream_stats(stream, TBB)
    assert stats["tool_calls"] == {"Skill": 1} and stats["n_skill_invocations"] == 0
    assert stats["offered_skills"] == [] and stats["offered_plugins"] == ["onetbb-quickstart"]


def test_a_stream_cut_before_its_result_has_no_result():
    """The last line of a killed CLI is an assistant event, not a result."""
    cut = "\n".join([_init("doctor"), _call("Bash", "a")])
    assert result_event(cut) is None
    assert solver_error(cut) == "no result object on stdout"
    half = cut + '\n{"type": "result", "num_tu'
    assert result_event(half) is None
    assert result_event(cut + "\n" + RESULT)["num_turns"] == 2
    # The bare `--output-format json` object (no type) still parses.
    assert result_event('{"num_turns": 1}') == {"num_turns": 1}


# ── recording the stream ──────────────────────────────────────────────────────


def test_record_stream_writes_both_files_and_flags_a_treatment_that_did_not_load(tmp_path):
    metrics: dict = {}
    record_stream("\n".join([_init("doctor"), RESULT]), tmp_path, metrics, TBB)
    assert json.loads((tmp_path / "solver.json").read_text())["type"] == "result"
    assert (tmp_path / "solver.jsonl").read_text().count("\n") == 1
    assert metrics["solver_error"].startswith("treatment not offered")


def test_loaded_is_read_from_init_plugins_not_the_slash_command_list(tmp_path):
    """A ``user-invocable: false`` skill is missing from init.skills but loads."""
    metrics: dict = {}
    record_stream("\n".join([_init("doctor", plugins=["onetbb-quickstart"]), RESULT]),
                  tmp_path, metrics, TBB)
    assert "solver_error" not in metrics
    other: dict = {}
    record_stream("\n".join([_init("onetbb-quickstart:onetbb-quickstart", plugins=[]), RESULT]),
                  tmp_path, other, TBB)
    assert other["solver_error"] == "treatment not offered: plugin onetbb-quickstart not in []"


def test_credentials_the_agent_prints_are_redacted_from_the_saved_stream(tmp_path):
    key = "sk-ant-api03-secretvalue"
    printed = _ev(type="user", message={"content": [
        {"type": "tool_result", "tool_use_id": "a", "content": f"ANTHROPIC_API_KEY={key}\nR=us"}]})
    proc = reply("\n".join([_init("doctor"), printed, RESULT]), f"debug {key}")

    class Log(list):
        write = list.append

    log = Log()
    from dokimi.harnesses.agent_sandbox import credential_values

    env = {"ANTHROPIC_API_KEY": key, "AWS_REGION": "us", "PATH": "/usr/bin"}
    record_solver(proc, "t", None, 1.0, tmp_path, [], log, {}, None, credential_values(env))
    saved = (tmp_path / "solver.jsonl").read_text(encoding="utf-8")
    assert key not in saved and "ANTHROPIC_API_KEY=[REDACTED]" in saved
    assert "R=us" in saved  # a short, non-secret value is left alone
    assert key not in "".join(log)


def test_values_the_stream_stores_as_json_escapes_are_redacted_too(tmp_path):
    """A header block with newlines, or a secret with a quote or backslash, is escaped in the stream."""
    from dokimi.harnesses.agent_sandbox import credential_values

    env = {"ANTHROPIC_CUSTOM_HEADERS": "X-Fake-A: hdrSecretAAAA1111\nX-Fake-B: hdrSecretBBBB2222",
           "AWS_SECRET_ACCESS_KEY": 'ab"cd\\efghijk'}
    printed = _ev(type="user", message={"content": [{"type": "tool_result", "tool_use_id": "a",
                                                     "content": " | ".join(env.values())}]})
    result = _ev(type="result", num_turns=2, result=env["AWS_SECRET_ACCESS_KEY"])
    proc = reply("\n".join([printed, result]))
    record_solver(proc, "t", None, 1.0, tmp_path, [], type("L", (), {"write": lambda *a: 0})(), {},
                  None, credential_values(env))
    for name in ("solver.jsonl", "solver.json"):
        saved = (tmp_path / name).read_text(encoding="utf-8")
        for piece in ("hdrSecretAAAA1111", "hdrSecretBBBB2222", "efghijk"):
            assert piece not in saved, (name, piece)
    assert "[REDACTED]" in (tmp_path / "solver.jsonl").read_text(encoding="utf-8")


def test_tokens_cover_every_model_the_cell_used(tmp_path):
    """``usage`` is the parent session only; a helper model or subagent is in modelUsage."""
    obj = {"type": "result", "num_turns": 2, "total_cost_usd": 0.02,
           "usage": {"input_tokens": 20, "output_tokens": 10},
           "modelUsage": {"claude-opus-5-5": {"inputTokens": 20, "outputTokens": 10,
                                              "cacheReadInputTokens": 3, "costUSD": 0.015},
                          "claude-haiku-4-5": {"inputTokens": 20, "outputTokens": 10,
                                               "cacheCreationInputTokens": 4, "costUSD": 0.005}}}
    usage = _parse_claude_json(json.dumps(obj), "t", "", 1.0)
    assert usage is not None
    assert (usage.prompt_tokens, usage.completion_tokens) == (40, 20)
    assert (usage.cache_read_tokens, usage.cache_write_tokens) == (3, 4)
    metrics: dict = {}

    class Log:
        def write(self, _):
            pass

    proc = reply(json.dumps(obj))
    record_solver(proc, "t", None, 1.0, tmp_path, [], Log(), metrics)
    assert metrics["models_used"] == ["claude-haiku-4-5", "claude-opus-5-5"]


def test_record_stream_without_a_result_leaves_solver_json_empty(tmp_path):
    metrics: dict = {}
    record_stream(_init("doctor"), tmp_path, metrics, None)
    assert (tmp_path / "solver.json").read_text() == ""
    assert metrics["solver_error"] == "no result object on stdout"


def test_a_loaded_treatment_records_no_error(tmp_path):
    metrics: dict = {}
    stream = "\n".join([_init("onetbb-quickstart:onetbb-quickstart"),
                        _call("Skill", "a", skill="onetbb-quickstart:onetbb-quickstart"),
                        _result_of("a"), RESULT])
    record_stream(stream, tmp_path, metrics, TBB)
    assert "solver_error" not in metrics
    assert metrics["n_skill_invocations"] == 1


def test_a_timed_out_solve_keeps_its_partial_stream(tmp_path, monkeypatch):
    partial = "\n".join([_init("onetbb-quickstart:onetbb-quickstart"),
                         _call("Skill", "a", skill="onetbb-quickstart:onetbb-quickstart"),
                         _result_of("a")]).encode()

    hung = FakeRunner(lambda call: reply(partial, timed_out=True))
    metrics: dict = {}
    with pytest.raises(subprocess.TimeoutExpired):
        run_solver(["claude"], tmp_path, metrics, TBB, timeout=1, env={}, runner=hung)
    assert (tmp_path / "solver.jsonl").read_bytes() == partial
    assert hung.calls[0].timeout == 1 and hung.calls[0].env == {}  # exactly the env it is given
    assert metrics["n_skill_invocations"] == 1
    # A timeout is a miss, not a tooling failure: it must not carry solver_error,
    # which the docs say to drop before comparing arms.
    assert "solver_error" not in metrics
    assert metrics["solver_timeout"] == "killed after 1s, no result event"


def test_a_timed_out_solve_without_its_plugin_is_still_a_solver_error(tmp_path, monkeypatch):
    partial = _init("doctor").encode()

    hung = FakeRunner(lambda call: reply(partial, timed_out=True))
    metrics: dict = {}
    with pytest.raises(subprocess.TimeoutExpired):
        run_solver(["claude"], tmp_path, metrics, TBB, timeout=1, env={}, runner=hung)
    assert metrics["solver_error"].startswith("treatment not offered")
    assert "solver_timeout" in metrics


def test_a_malformed_token_count_reads_as_zero():
    obj = {"type": "result", "modelUsage": {"m": {"inputTokens": "n/a", "outputTokens": 3}}}
    usage = _parse_claude_json(json.dumps(obj), "t", "", 1.0)
    assert usage is not None and (usage.prompt_tokens, usage.completion_tokens) == (0, 3)
