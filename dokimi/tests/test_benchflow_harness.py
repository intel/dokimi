# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Tests for the BenchFlow (``bench eval run``) harness adapter.

``bench`` is never executed: the harness gets a :class:`_Bench` runner standing
for the ``bench`` and ``docker`` CLIs, whose bench writes a ``result.json``
shaped like the one benchflow 0.7.8 wrote for a real claude-agent-acp rollout,
so the tests cover command construction, skill staging and the result →
``task_runs.v1`` mapping. The autouse fixture clears the proxy variables. Only
the ``_run_bench`` tests start real processes. Error categories are the ones
0.7.8 writes (``acp_error``, ``timeout``, ``idle_timeout``).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

import dokimi.harnesses.benchflow as _bf
from dokimi.artifacts import validate_artifact
from dokimi.harnesses import PreflightError, TaskSuiteRunner, load_task
from dokimi.harnesses.benchflow import (
    BenchflowHarness,
    _subprocess_env,
    parse_result,
    rollout_results,
    skill_loads,
)
from dokimi.harnesses.registry import create_harness, known_harnesses
from dokimi.process import FakeRunner, reply

# Trimmed from a real rollout (haiku-4.5 on onetbb-histogram-local-aggregation).
_RESULT = {
    "task_name": "sb-task",
    "rewards": {"reward": 1.0},
    "agent": "claude-agent-acp",
    "skill_mode": "no-skill",
    "skill_source": "none",
    "n_tool_calls": 6,
    "n_skill_invocations": 0,
    "agent_result": {
        "n_input_tokens": 220000,
        "n_output_tokens": 8225,
        "n_cache_read_tokens": 150000,
        "n_cache_creation_tokens": 20000,
        "total_tokens": 228225,
        "cost_usd": 0.0461296,
        "usage_source": "provider_response",
    },
    "error": None,
    "error_category": None,
    "timing": {"environment_setup": 30.1, "total": 93.7},
    "task_digest": "sha256:abc",
}


_PROXY_ENV = ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "no_proxy", "NO_PROXY")


@pytest.fixture(autouse=True)
def _hermetic(monkeypatch):
    """No host proxy settings, so _subprocess_env makes no `docker network inspect`."""
    for var in _PROXY_ENV:
        monkeypatch.delenv(var, raising=False)


def _gateway(answer: str | None) -> _Bench:
    """A docker whose `network inspect bridge` reports ``answer`` (None: no such network)."""
    return _Bench(docker=lambda argv: reply(f"{answer}\n") if answer else reply(returncode=1))


def _task(root: Path, *, skills: bool = True) -> Path:
    task = root / "sb-task"
    (task / "environment").mkdir(parents=True)
    (task / "oracle").mkdir()
    (task / "verifier").mkdir()
    if skills:
        (task / "environment" / "skills" / "pack").mkdir(parents=True)
        (task / "environment" / "skills" / "pack" / "SKILL.md").write_text("x", encoding="utf-8")
    (task / "task.md").write_text(
        "---\nschema_version: '1.3'\nagent:\n  timeout_sec: 600\n---\nDo it.\n", encoding="utf-8"
    )
    return task


class _Bench(FakeRunner):
    """Stands for the ``bench`` and ``docker`` CLIs.

    ``bench eval run`` calls ``on_bench(cmd, cwd)``, which writes what bench would
    and returns ``(returncode, stdout, stderr[, timed_out])`` (``None``: exit 0).
    ``docker`` answers from ``docker(argv)`` (``None``: exit 0, no output, so the
    bridge lookup finds no gateway). ``bench["cmd"]`` & co. are the last bench call.
    """

    def __init__(self, on_bench=None, docker=None):
        super().__init__(self._answer)
        self.on_bench, self.docker, self.seen = on_bench, docker, {}

    def _answer(self, call):
        argv = list(call.argv)
        if argv[:3] == ["bench", "eval", "run"]:
            jobs = Path(argv[argv.index("--jobs-dir") + 1])
            self.seen.update(cmd=argv, env=call.env, timeout=call.timeout, cwd=call.cwd,
                             term_grace=call.term_grace, jobs_existed=jobs.exists())
            out = self.on_bench(argv, call.cwd) if self.on_bench else None
            if out is None:
                return None
            returncode, stdout, stderr, *rest = out
            return reply(stdout, stderr, returncode, timed_out=bool(rest and rest[0]))
        if argv[0] == "docker" and self.docker is not None:
            return self.docker(argv)
        return None

    def __getitem__(self, key):
        return self.seen[key]

    def __contains__(self, key):
        return key in self.seen

    def docker_calls(self) -> list[list[str]]:
        return [list(c.argv) for c in self.calls if c.argv[0] == "docker"]


def _writes_rollout(result: dict | None, returncode: int = 0):
    """``on_bench`` for a bench that writes one rollout ``result.json`` (none when ``result`` is None)."""

    def write(cmd, cwd):
        if result is not None:
            rollout = Path(cmd[cmd.index("--jobs-dir") + 1]) / "2026-10-03__20-00-00" / "sb-task__abcd"
            rollout.mkdir(parents=True)
            (rollout / "result.json").write_text(json.dumps(result), encoding="utf-8")
        return returncode, "✓ Score: 1/1\n", ""

    return write


def _fake_bench(monkeypatch, result: dict | None, returncode: int = 0) -> _Bench:
    """A :class:`_Bench` that writes one rollout ``result.json``, with the preflight skipped:
    a suite run that is not given this runner then starts nothing before its first cell."""
    monkeypatch.setattr(_bf.BenchflowHarness, "preflight", lambda self: None)
    return _Bench(_writes_rollout(result, returncode))


# ── registry ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "harness_id, mode, skills",
    [
        ("benchflow:claude-agent-acp", "no-skill", None),
        ("benchflow-skill:claude-agent-acp", "with-skill", None),
        ("benchflow-skill:codex-acp:/x/skills", "with-skill", "/x/skills"),
        ("benchflow-selfgen:gemini", "self-gen", None),
        ("benchflow:oracle", "no-skill", None),
    ],
)
def test_registry_parses_benchflow_ids(harness_id, mode, skills):
    h = create_harness(harness_id)
    assert isinstance(h, BenchflowHarness)
    assert h.skill_mode == mode
    assert h.skills_dir == skills


@pytest.mark.parametrize(
    "harness_id, agent, skills",
    [("benchflow:acp:pi", "acp:pi", None),
     ("benchflow-skill:acp:pi", "acp:pi", None),
     ("benchflow-skill:ai-sdk:codex:/x/skills", "ai-sdk:codex", "/x/skills"),
     ("benchflow-skill:omnigent:pi:/x", "omnigent:pi", "/x")],
)
def test_registry_keeps_namespaced_agent_ids_whole(harness_id, agent, skills):
    h = create_harness(harness_id)
    assert (h.agent, h.skills_dir) == (agent, skills)


def test_acpx_is_not_a_namespace():
    """0.7.8 writes acpx/<id>; acpx:<x> would be a skills dir like any agent's."""
    assert create_harness("benchflow:acpx/claude").agent == "acpx/claude"
    assert create_harness("benchflow-skill:acpx:/x").skills_dir == "/x"


def test_selfgen_passes_an_explicit_skill_creator(tmp_path):
    task = load_task(_task(tmp_path))
    creator = tmp_path / "skill-creator"
    cmd = create_harness(f"benchflow-selfgen:claude-agent-acp:{creator}").build_command(
        task, model=None, jobs_dir=tmp_path
    )
    assert cmd[cmd.index("--skill-creator-dir") + 1] == str(creator)
    assert "--skill-creator-dir" not in create_harness("benchflow-selfgen:x").build_command(
        task, model=None, jobs_dir=tmp_path)


def _creator(path: Path, desc: str) -> Path:
    path.mkdir(parents=True)
    (path / "SKILL.md").write_text(f"---\nname: {path.name}\ndescription: {desc}\n---\n",
                                   encoding="utf-8")
    return path


def _staged_creator(seen: dict) -> Path:
    return Path(seen["cmd"][seen["cmd"].index("--skill-creator-dir") + 1])


def test_named_creator_is_staged_alone_not_next_to_a_stock_one(tmp_path, monkeypatch):
    """BenchFlow prefers a `skill-creator` pack beside the one it is given."""
    task = load_task(_task(tmp_path))
    custom = _creator(tmp_path / "skills" / "my-creator", "CUSTOM")
    _creator(tmp_path / "skills" / "skill-creator", "STOCK")
    seen = _fake_bench(monkeypatch, _RESULT)
    create_harness(f"benchflow-selfgen:a:{custom}", runner=seen).run(task, output_dir=tmp_path / "o")
    staged = _staged_creator(seen)
    assert staged == tmp_path / "o" / "creator" / "my-creator"
    assert [p.name for p in staged.parent.iterdir()] == ["my-creator"]
    assert "CUSTOM" in (staged / "SKILL.md").read_text(encoding="utf-8")


def test_a_symlinked_creator_is_not_resolved_to_its_targets_neighbours(tmp_path, monkeypatch):
    task = load_task(_task(tmp_path))
    custom = _creator(tmp_path / "skills" / "my-creator", "CUSTOM")
    _creator(tmp_path / "skills" / "skill-creator", "STOCK")
    link = tmp_path / "creator-link"
    link.symlink_to(custom)
    seen = _fake_bench(monkeypatch, _RESULT)
    create_harness(f"benchflow-selfgen:a:{link}", runner=seen).run(task, output_dir=tmp_path / "o")
    staged = _staged_creator(seen)
    assert [p.name for p in staged.parent.iterdir()] == ["my-creator"]  # the pack, not the link
    assert not staged.is_symlink()
    assert "CUSTOM" in (staged / "SKILL.md").read_text(encoding="utf-8")


def test_dry_run_shows_the_staged_creator_and_copies_nothing(tmp_path, monkeypatch):
    task = load_task(_task(tmp_path))
    custom = _creator(tmp_path / "skills" / "my-creator", "CUSTOM")
    res = create_harness(f"benchflow-selfgen:a:{custom}").run(
        task, output_dir=tmp_path / "o", dry_run=True)
    assert res.command[res.command.index("--skill-creator-dir") + 1] == str(
        tmp_path / "o" / "creator" / "my-creator")
    assert not (tmp_path / "o" / "creator").exists()


@pytest.mark.parametrize("bad", ["benchflow:", "benchflow-skill:", "benchflow:claude:/x"])
def test_registry_rejects_malformed_benchflow_ids(bad):
    with pytest.raises(ValueError):
        create_harness(bad)


def test_known_harnesses_lists_benchflow():
    assert any(k.startswith("benchflow:") for k in known_harnesses())


# ── command ───────────────────────────────────────────────────────────────────


def test_command_no_skill(tmp_path):
    task = load_task(_task(tmp_path))
    cmd = create_harness("benchflow:claude-agent-acp").build_command(
        task, model="aws-bedrock/us.anthropic.claude-haiku-4-5", jobs_dir=tmp_path / "j"
    )
    assert cmd[:3] == ["bench", "eval", "run"]
    assert cmd[cmd.index("--tasks-dir") + 1] == str(task.path)
    assert cmd[cmd.index("--skill-mode") + 1] == "no-skill"
    assert cmd[cmd.index("--model") + 1] == "aws-bedrock/us.anthropic.claude-haiku-4-5"
    assert "--skills-dir" not in cmd


def test_no_idle_watchdog_only_the_task_budget(tmp_path):
    """BenchFlow's 600 s idle default would end a 1800 s-budget cell after a slow
    model call (measured: solved, then 0 after 600 s idle); docker-* has no such limit."""
    task = load_task(_task(tmp_path))
    for hid in ("benchflow:claude-agent-acp", "benchflow-skill:x", "benchflow-selfgen:x"):
        cmd = create_harness(hid).build_command(task, model=None, jobs_dir=tmp_path)
        assert cmd[cmd.index("--agent-idle-timeout") + 1] == "0"


def test_oracle_gets_no_model(tmp_path):
    task = load_task(_task(tmp_path))
    cmd = create_harness("benchflow:oracle").build_command(task, model="m", jobs_dir=tmp_path)
    assert "--model" not in cmd


def test_with_skill_defaults_to_task_bundled_skills(tmp_path):
    task = load_task(_task(tmp_path))
    cmd = create_harness("benchflow-skill:claude-agent-acp").build_command(
        task, model=None, jobs_dir=tmp_path
    )
    assert cmd[cmd.index("--skills-dir") + 1] == str(task.path / "environment" / "skills")


def test_with_skill_without_any_skills_names_the_fix(tmp_path):
    task = load_task(_task(tmp_path, skills=False))
    with pytest.raises(ValueError, match="benchflow-skill:<agent>:<skills_dir>"):
        create_harness("benchflow-skill:claude-agent-acp").build_command(
            task, model=None, jobs_dir=tmp_path
        )


def test_single_external_skill_is_staged_as_a_root(tmp_path, monkeypatch):
    """A skill dir is wrapped in a root; symlinks are copied as real files."""
    task = load_task(_task(tmp_path, skills=False))
    real = tmp_path / "src" / "uxl-onetbb"
    (real / "references").mkdir(parents=True)
    (real / "SKILL.md").write_text("skill", encoding="utf-8")
    (real / "references" / "r.md").write_text("ref", encoding="utf-8")
    link = tmp_path / "link"
    link.symlink_to(real)
    seen = _fake_bench(monkeypatch, _RESULT)
    out = tmp_path / "out"
    create_harness(f"benchflow-skill:claude-agent-acp:{link}", runner=seen).run(task, output_dir=out)
    staged = Path(seen["cmd"][seen["cmd"].index("--skills-dir") + 1])
    assert staged == out / "skills"
    assert (staged / "uxl-onetbb" / "SKILL.md").read_text(encoding="utf-8") == "skill"
    assert not (staged / "uxl-onetbb").is_symlink()


def test_dry_run_shows_the_staged_skills_dir_and_runs_nothing(tmp_path, monkeypatch):
    task = load_task(_task(tmp_path, skills=False))
    pack = tmp_path / "pack"
    pack.mkdir()
    (pack / "SKILL.md").write_text("s", encoding="utf-8")
    nothing = FakeRunner(lambda call: pytest.fail(f"a dry run ran {call.argv}"))
    out = tmp_path / "o"
    res = create_harness(f"benchflow-skill:a:{pack}", runner=nothing).run(task, output_dir=out, dry_run=True)
    assert res.command[res.command.index("--skills-dir") + 1] == str(out / "skills")
    assert not (out / "skills").exists()
    assert res.metrics["dry_run"] is True and res.reward is None


def test_symlinks_inside_a_staged_skill_become_real_files(tmp_path, monkeypatch):
    """copytree(symlinks=True) would stage links that do not resolve in the image."""
    task = load_task(_task(tmp_path, skills=False))
    shared = tmp_path / "shared.md"
    shared.write_text("shared ref", encoding="utf-8")
    pack = tmp_path / "src" / "uxl-onetbb"
    pack.mkdir(parents=True)
    (pack / "SKILL.md").write_text("skill", encoding="utf-8")
    (pack / "ref.md").symlink_to(shared)
    seen = _fake_bench(monkeypatch, _RESULT)
    create_harness(f"benchflow-skill:a:{pack}", runner=seen).run(task, output_dir=tmp_path / "o")
    staged = Path(seen["cmd"][seen["cmd"].index("--skills-dir") + 1]) / "uxl-onetbb" / "ref.md"
    assert not staged.is_symlink() and staged.read_text(encoding="utf-8") == "shared ref"


def test_a_root_whose_pack_is_a_symlink_is_staged_as_real_dirs(tmp_path, monkeypatch):
    task = load_task(_task(tmp_path, skills=False))
    real = tmp_path / "elsewhere" / "uxl-onetbb"
    real.mkdir(parents=True)
    (real / "SKILL.md").write_text("skill", encoding="utf-8")
    root = tmp_path / "root"
    root.mkdir()
    (root / "uxl-onetbb").symlink_to(real)
    seen = _fake_bench(monkeypatch, _RESULT)
    create_harness(f"benchflow-skill:a:{root}", runner=seen).run(task, output_dir=tmp_path / "o")
    staged = Path(seen["cmd"][seen["cmd"].index("--skills-dir") + 1]) / "uxl-onetbb"
    assert not staged.is_symlink() and (staged / "SKILL.md").read_text(encoding="utf-8") == "skill"


def test_rerun_restages_skills_from_scratch(tmp_path, monkeypatch):
    task = load_task(_task(tmp_path, skills=False))
    pack = tmp_path / "pack"
    pack.mkdir()
    (pack / "SKILL.md").write_text("s", encoding="utf-8")
    out = tmp_path / "o"
    (out / "skills" / "old-pack").mkdir(parents=True)
    (out / "skills" / "old-pack" / "SKILL.md").write_text("stale", encoding="utf-8")
    bench = _fake_bench(monkeypatch, _RESULT)
    create_harness(f"benchflow-skill:a:{pack}", runner=bench).run(task, output_dir=out)
    assert sorted(p.name for p in (out / "skills").iterdir()) == ["pack"]


def test_skill_md_path_is_accepted(tmp_path, monkeypatch):
    task = load_task(_task(tmp_path, skills=False))
    pack = tmp_path / "pack"
    pack.mkdir()
    (pack / "SKILL.md").write_text("s", encoding="utf-8")
    seen = _fake_bench(monkeypatch, _RESULT)
    create_harness(f"benchflow-skill:a:{pack / 'SKILL.md'}", runner=seen).run(task, output_dir=tmp_path / "o")
    staged = Path(seen["cmd"][seen["cmd"].index("--skills-dir") + 1])
    assert (staged / "pack" / "SKILL.md").is_file()


def test_skills_root_is_copied_whole(tmp_path, monkeypatch):
    task = load_task(_task(tmp_path, skills=False))
    root = tmp_path / "root"
    for name in ("a", "b"):
        (root / name).mkdir(parents=True)
        (root / name / "SKILL.md").write_text(name, encoding="utf-8")
    seen = _fake_bench(monkeypatch, _RESULT)
    create_harness(f"benchflow-skill:x:{root}", runner=seen).run(task, output_dir=tmp_path / "o")
    staged = Path(seen["cmd"][seen["cmd"].index("--skills-dir") + 1])
    assert sorted(p.name for p in staged.iterdir()) == ["a", "b"]


def test_a_root_without_packs_is_refused_before_bench_runs(tmp_path, monkeypatch):
    """A checkout one level above skills/ would deploy no skill, silently."""
    task = load_task(_task(tmp_path, skills=False))
    checkout = tmp_path / "uxl-skills"
    (checkout / "skills" / "uxl-onetbb").mkdir(parents=True)
    (checkout / "skills" / "uxl-onetbb" / "SKILL.md").write_text("s", encoding="utf-8")
    bench = _Bench(lambda cmd, cwd: pytest.fail("bench must not run"))
    with pytest.raises(ValueError, match="holds no <name>/SKILL.md pack"):
        create_harness(f"benchflow-skill:a:{checkout}", runner=bench).run(task, output_dir=tmp_path / "o")
    # the right level runs
    bench = _fake_bench(monkeypatch, _RESULT)
    res = create_harness(f"benchflow-skill:a:{checkout / 'skills'}", runner=bench).run(
        task, output_dir=tmp_path / "o2")
    assert res.reward == 1.0


def test_task_bundled_skills_without_packs_are_refused(tmp_path):
    path = _task(tmp_path, skills=False)
    (path / "environment" / "skills" / "notes").mkdir(parents=True)
    with pytest.raises(ValueError, match="holds no <name>/SKILL.md pack"):
        create_harness("benchflow-skill:a").run(load_task(path), output_dir=tmp_path / "o",
                                                dry_run=True)


def test_selfgen_refuses_a_creator_dir_benchflow_would_not_use(tmp_path, monkeypatch):
    task = load_task(_task(tmp_path))
    bench = _fake_bench(monkeypatch, _RESULT)
    with pytest.raises(FileNotFoundError, match="no skill-creator SKILL.md"):
        create_harness(f"benchflow-selfgen:a:{tmp_path / 'typo'}", runner=bench).run(
            task, output_dir=tmp_path / "o")
    creator = tmp_path / "skills" / "skill-creator"
    creator.mkdir(parents=True)
    (creator / "SKILL.md").write_text("---\nname: skill-creator\n---\n", encoding="utf-8")
    for given in (creator, creator.parent):  # the dir itself, or a root holding it
        res = create_harness(f"benchflow-selfgen:a:{given}", runner=bench).run(task, output_dir=tmp_path / "o")
        assert res.reward == 1.0


# ── result parsing ────────────────────────────────────────────────────────────


def test_parse_result_maps_reward_tokens_cost_and_skill_use():
    reward, m = parse_result(_RESULT)
    assert reward == 1.0
    assert m["total_tokens"] == 228225
    assert m["prompt_tokens"] == 220000
    assert m["cache_read_tokens"] == 150000
    assert m["cost_usd"] == pytest.approx(0.0461296)
    assert m["n_tool_calls"] == 6
    assert m["n_skill_invocations_benchflow"] == 0
    assert "n_skill_invocations" not in m  # treatment-only; set by skill_loads
    assert "error" not in m


def test_error_before_verifier_is_missing_data():
    """A dead key (0.7.8: acp_error) / install failure leave no reward: keep it out of McNemar."""
    reward, m = parse_result({
        **_RESULT, "rewards": None, "error": "ACP error -32603: Internal error",
        "error_category": "acp_error",
        "agent_result": {"total_tokens": 0, "cost_usd": None},
    })
    assert reward is None
    assert m["error"].startswith("benchflow: acp_error")
    assert "cost_usd" not in m


def test_error_with_a_reward_is_still_a_verdict():
    reward, m = parse_result({**_RESULT, "rewards": {"reward": 0.0},
                              "error": "Agent prompt exceeded wall-clock budget 600s",
                              "error_category": "timeout"})
    assert reward == 0.0
    assert "error" not in m
    assert m["benchflow_error_category"] == "timeout"


def test_unavailable_usage_is_not_zero_tokens():
    """BenchFlow writes 0s when it captured no usage; the row must not say 0."""
    reward, m = parse_result({**_RESULT, "agent_result": {
        "n_input_tokens": 0, "n_output_tokens": 0, "total_tokens": 0, "cost_usd": 0.0,
        "usage_source": "unavailable"}})
    assert reward == 1.0 and m["usage_source"] == "unavailable"
    for key in ("prompt_tokens", "completion_tokens", "total_tokens", "cost_usd"):
        assert key not in m


def test_parse_result_none():
    assert parse_result(None) == (None, {})


def test_rollout_results_are_oldest_first(tmp_path):
    old = tmp_path / "j2" / "t__1" / "result.json"
    new = tmp_path / "j1" / "t__2" / "result.json"
    for i, p in enumerate((old, new)):
        p.parent.mkdir(parents=True)
        p.write_text("{}", encoding="utf-8")
        os.utime(p, (1000 + i, 1000 + i))
    assert rollout_results(tmp_path) == [old, new]
    assert rollout_results(tmp_path / "missing") == []


def test_verifier_error_without_a_reward_says_what_failed():
    reward, m = parse_result({**_RESULT, "rewards": None,
                              "verifier_error": "test.sh exited 2 before writing a reward",
                              "verifier_error_category": "verifier_infra"})
    assert reward is None
    assert m["error"] == "benchflow: verifier_infra: test.sh exited 2 before writing a reward"


# ── run ───────────────────────────────────────────────────────────────────────


def test_run_folds_result_into_harness_result(tmp_path, monkeypatch):
    task = load_task(_task(tmp_path))
    seen = _fake_bench(monkeypatch, _RESULT)
    res = create_harness("benchflow:claude-agent-acp", runner=seen).run(
        task, model="m", output_dir=tmp_path / "o"
    )
    assert res.passed and res.reward == 1.0
    assert res.metrics["cost_usd"] == pytest.approx(0.0461296)
    assert res.metrics["skill_mode"] == "no-skill"
    # BenchFlow's hard deadline (agent + verifier 600 + build 600 + install 900
    # + margin 1800) plus job overhead 600
    assert seen["timeout"] == 600 + 600 + 600 + 900 + 1800 + 600
    assert seen["cmd"][seen["cmd"].index("--retry-attempts") + 1] == "0"
    assert res.metrics["n_attempts"] == 1
    assert (tmp_path / "o" / "bench.log").is_file()


def test_partial_reward_is_kept_but_is_not_a_pass(tmp_path, monkeypatch):
    task = load_task(_task(tmp_path))
    bench = _fake_bench(monkeypatch, {**_RESULT, "rewards": {"reward": 0.667}})
    res = create_harness("benchflow:claude-agent-acp", runner=bench).run(task, model="m", output_dir=tmp_path / "o")
    assert res.reward == pytest.approx(0.667) and res.passed is False
    assert "error" not in res.metrics


def test_run_keeps_benchflows_own_error(tmp_path, monkeypatch):
    task = load_task(_task(tmp_path))
    bench = _fake_bench(monkeypatch, {**_RESULT, "rewards": None, "error": "ACP error -32603",
                              "error_category": "acp_error"}, returncode=1)
    res = create_harness("benchflow:claude-agent-acp", runner=bench).run(task, model="m", output_dir=tmp_path / "o")
    assert res.reward is None
    assert res.metrics["error"] == "benchflow: acp_error: ACP error -32603"


def test_run_without_result_is_an_error_cell(tmp_path, monkeypatch):
    task = load_task(_task(tmp_path))
    bench = _fake_bench(monkeypatch, None, returncode=1)
    res = create_harness("benchflow:claude-agent-acp", runner=bench).run(task, output_dir=tmp_path / "o")
    assert res.reward is None and not res.passed
    assert "no BenchFlow result.json" in res.metrics["error"]


def test_bench_failing_before_a_rollout_names_its_first_stderr_line(tmp_path, monkeypatch):
    """E.g. uxl's answer-quality tasks: BenchFlow rejects their tests/rubric.json."""
    task = load_task(_task(tmp_path))
    msg = "Error: tests/rubric.json is not a valid rubric: 5 validation errors for Rubric"
    bench = _Bench(lambda cmd, cwd: (1, "", f"\n{msg}\n  criteria\n"))
    res = create_harness("benchflow:oracle", runner=bench).run(task, output_dir=tmp_path / "o")
    assert res.metrics["error"] == f"no BenchFlow result.json (exit 1): {msg}"


def test_bridge_hosts_bypass_the_proxy(monkeypatch):
    monkeypatch.setenv("https_proxy", "http://proxy.example:912/")
    monkeypatch.setenv("no_proxy", "localhost,intel.com")
    env = _subprocess_env({}, runner=_gateway("10.200.0.1"))  # a custom bip
    assert env["no_proxy"].split(",") == [
        "localhost", "intel.com", "10.200.0.1", "172.17.0.1", "host.docker.internal",
    ]
    assert env["NO_PROXY"] == env["no_proxy"]


@pytest.mark.parametrize("var", ["NO_PROXY", "no_proxy"])
def test_one_spelling_of_no_proxy_is_kept_in_both(monkeypatch, var):
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.example:912/")
    monkeypatch.setenv(var, "localhost,127.0.0.1,intel.com")
    env = _subprocess_env({}, runner=_gateway("172.17.0.1"))
    for out in ("no_proxy", "NO_PROXY"):
        assert env[out].split(",") == [
            "localhost", "127.0.0.1", "intel.com", "172.17.0.1", "host.docker.internal",
        ]


def test_a_bypass_everything_no_proxy_stays_star(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.example:912/")
    monkeypatch.setenv("NO_PROXY", "*")
    env = _subprocess_env({}, runner=_gateway("172.17.0.1"))
    assert env["no_proxy"] == env["NO_PROXY"] == "*"


def test_a_star_in_one_spelling_does_not_replace_the_others_list(monkeypatch):
    """urllib reads no_proxy first: a list there next to NO_PROXY=* must stay a list."""
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.example:912/")
    monkeypatch.setenv("no_proxy", "localhost,.intel.com")
    monkeypatch.setenv("NO_PROXY", "*")
    env = _subprocess_env({}, runner=_gateway(None))
    assert env["NO_PROXY"] == "*"
    assert env["no_proxy"].split(",") == ["localhost", ".intel.com", "172.17.0.1", "host.docker.internal"]


def test_no_proxy_untouched_without_a_proxy(monkeypatch):
    assert "no_proxy" not in _subprocess_env({})


def test_suite_artifact_validates_and_pairs_arms(tmp_path, monkeypatch):
    task = load_task(_task(tmp_path))
    bench = _fake_bench(monkeypatch, _RESULT)
    report = TaskSuiteRunner(
        tasks=[task],
        harnesses=["benchflow:claude-agent-acp", "benchflow-skill:claude-agent-acp"],
        baseline_harness="benchflow:claude-agent-acp",
        output_root=tmp_path / "runs",
        runner=bench,
    ).run()
    validate_artifact("task_runs", report)
    stats = report["summary"]["per_harness"]["benchflow-skill:claude-agent-acp"]
    assert stats["pass_rate"] == 1.0
    assert stats["cost"]["total_cost_usd"] == pytest.approx(0.0461296, abs=1e-6)
    assert report["tasks"][0]["format"] == "task-md"


# ── skill loads from the trajectory ───────────────────────────────────────────

# The exact event claude-agent-acp 0.73.0 emitted in a real with-skill rollout.
_LOAD_SKILL = {
    "type": "tool_call", "tool_call_id": "toolu_x", "kind": "other", "title": "Load skill",
    "status": "completed", "content": [], "raw_input": {"skill": "uxl-onetbb"},
    "raw_output": "Launching skill: uxl-onetbb",
}


def _trajectory(rollout: Path, events: list[dict], sub: str = "trajectory") -> None:
    (rollout / sub).mkdir(parents=True, exist_ok=True)
    (rollout / sub / "acp_trajectory.jsonl").write_text(
        "\n".join(json.dumps(e) for e in events) + "\nnot json\n", encoding="utf-8"
    )


def test_skill_loads_counts_what_benchflow_misses(tmp_path):
    _trajectory(tmp_path, [
        {"type": "agent_thought", "text": "use the uxl-onetbb skill"},
        _LOAD_SKILL,
        {"type": "tool_call", "kind": "read", "title": "Read SKILL.md", "raw_input": {"path": "x"}},
    ])
    out = skill_loads(tmp_path, reported=0, treatment=frozenset({"uxl-onetbb"}))
    assert out == {"skills_loaded": ["uxl-onetbb"], "n_skill_invocations_benchflow": 0,
                   "n_skill_invocations": 1}


def test_skill_loads_counts_the_treatment_only(tmp_path):
    """#100: a treatment load once and a distractor twice is 1, not 3."""
    distractor = {**_LOAD_SKILL, "raw_input": {"skill": "other-pack"},
                  "raw_output": "Launching skill: other-pack"}
    _trajectory(tmp_path, [
        _LOAD_SKILL,
        {**distractor, "tool_call_id": "d1"},
        {**distractor, "tool_call_id": "d2"},
    ])
    out = skill_loads(tmp_path, reported=3, treatment=frozenset({"uxl-onetbb"}))
    assert out["skills_loaded"] == ["uxl-onetbb", "other-pack", "other-pack"]
    assert out["n_skill_invocations"] == 1
    assert out["n_skill_invocations_benchflow"] == 3


def test_skill_loads_without_a_known_treatment_reports_no_count(tmp_path):
    _trajectory(tmp_path, [_LOAD_SKILL], sub="agent")
    out = skill_loads(tmp_path, reported=2)
    assert "n_skill_invocations" not in out and out["n_skill_invocations_benchflow"] == 2


def test_skill_loads_reads_calls_benchflow_classified(tmp_path):
    """BenchFlow rewrites a call it recognises to kind "skill" (gemini, opencode, ...)."""
    _trajectory(tmp_path, [{**_LOAD_SKILL, "kind": "skill", "raw_output": None},
                           {**_LOAD_SKILL, "kind": "skill", "raw_output": None}])  # same id
    out = skill_loads(tmp_path, reported=1, treatment=frozenset({"uxl-onetbb"}))
    assert out["skills_loaded"] == ["uxl-onetbb"] and out["n_skill_invocations"] == 1


def test_skill_loads_counts_an_unclassified_call_only_in_claudes_shape(tmp_path):
    """An MCP tool that happens to take a "skill" argument is not a skill load."""
    mcp = {"type": "tool_call", "tool_call_id": "m1", "kind": "other", "title": "query_catalog",
           "status": "completed", "raw_input": {"skill": "pack"}, "raw_output": [{"rows": 3}]}
    _trajectory(tmp_path, [mcp])
    assert skill_loads(tmp_path, reported=0, treatment=frozenset({"pack"}))["n_skill_invocations"] == 0
    load = {**mcp, "tool_call_id": "c1", "title": "Load skill", "raw_output": None}
    _trajectory(tmp_path, [mcp, load])
    out = skill_loads(tmp_path, reported=0, treatment=frozenset({"pack"}))
    assert out["skills_loaded"] == ["pack"] and out["n_skill_invocations"] == 1


def test_skill_loads_without_trajectory_is_empty(tmp_path):
    assert skill_loads(tmp_path, reported=0) == {}


def test_run_folds_trajectory_skill_loads(tmp_path, monkeypatch):
    task = load_task(_task(tmp_path))

    def bench(cmd, cwd):
        rollout = Path(cmd[cmd.index("--jobs-dir") + 1]) / "job" / "sb-task__1"
        rollout.mkdir(parents=True)
        (rollout / "result.json").write_text(json.dumps(_RESULT), encoding="utf-8")
        pack = {**_LOAD_SKILL, "tool_call_id": "p1", "raw_input": {"skill": "pack"},
                "raw_output": "Launching skill: pack"}
        _trajectory(rollout, [_LOAD_SKILL, pack])
        return 0, "", ""

    res = create_harness("benchflow-skill:claude-agent-acp", runner=_Bench(bench)).run(
        task, output_dir=tmp_path / "o")
    # the task bundles one pack, "pack": uxl-onetbb is not this arm's treatment
    assert res.metrics["n_skill_invocations"] == 1
    assert res.metrics["skills_loaded"] == ["uxl-onetbb", "pack"]


# ── reruns, missing rewards, timeouts ─────────────────────────────────────────


def test_rerun_that_crashes_does_not_reuse_the_previous_reward(tmp_path, monkeypatch):
    """A stale result.json in the deterministic cell dir must not read as a pass."""
    task = load_task(_task(tmp_path))
    out = tmp_path / "o"
    stale = out / "jobs" / "2026-10-01" / "sb-task__old"
    stale.mkdir(parents=True)
    (stale / "result.json").write_text(json.dumps(_RESULT), encoding="utf-8")
    seen = _fake_bench(monkeypatch, None, returncode=1)
    res = create_harness("benchflow:claude-agent-acp", runner=seen).run(task, output_dir=out)
    assert seen["jobs_existed"] is False
    assert res.reward is None and not res.passed
    assert res.metrics["error"] == "no BenchFlow result.json (exit 1)"


def test_rerun_whose_previous_jobs_dir_cannot_be_cleared_does_not_run(tmp_path, monkeypatch):
    """A rollout that cannot be deleted (root-owned) must not be read back as this run's."""
    import shutil

    task = load_task(_task(tmp_path))
    runs = tmp_path / "runs"
    stale = runs / "sb-task" / "benchflow_claude-agent-acp" / "jobs" / "2026-10-01" / "sb-task__old"
    stale.mkdir(parents=True)
    (stale / "result.json").write_text(json.dumps(_RESULT), encoding="utf-8")
    seen = _fake_bench(monkeypatch, None, returncode=1)

    def cannot_delete(path, ignore_errors=False, onerror=None, **kwargs):
        if not (ignore_errors or onerror):  # with a hook, its retry fails too
            raise PermissionError(13, "Permission denied", str(path))

    monkeypatch.setattr(shutil, "rmtree", cannot_delete)
    output = TaskSuiteRunner(
        tasks=[task], harnesses=["benchflow:claude-agent-acp"], output_root=runs
    ).run()

    [row] = output["results"]
    assert (row["passed"], row["reward"]) == (False, None), "the stale rollout was read as this run's"
    assert "could not clear" in row["metrics"]["error"]
    assert "cmd" not in seen  # bench never ran
    assert output["summary"]["per_harness"]["benchflow:claude-agent-acp"]["errored"] == 1


def test_result_without_reward_says_so(tmp_path, monkeypatch):
    task = load_task(_task(tmp_path))
    bench = _fake_bench(monkeypatch, {**_RESULT, "rewards": {}}, returncode=0)
    res = create_harness("benchflow:claude-agent-acp", runner=bench).run(task, output_dir=tmp_path / "o")
    assert res.metrics["error"] == "BenchFlow result.json has no rewards.reward (exit 0)"


def test_timeout_removes_only_this_cells_rollout_containers(tmp_path, monkeypatch):
    task = load_task(_task(tmp_path))

    def killed(cmd, cwd):
        (Path(cmd[cmd.index("--jobs-dir") + 1]) / "job" / "sb-task__dead").mkdir(parents=True)
        return _bf._TIMEOUT_RC, "", "", True

    bench = _Bench(killed, docker=lambda argv: reply("c1\nc2\n" if argv[1] == "ps" else ""))
    res = create_harness("benchflow:claude-agent-acp", runner=bench).run(task, output_dir=tmp_path / "o")
    assert bench["term_grace"] == _bf._KILL_GRACE_SEC  # bench gets SIGTERM before SIGKILL
    docker_calls = bench.docker_calls()
    assert docker_calls[0] == [
        "docker", "ps", "-aq", "--filter", "label=com.docker.compose.project=sb-task__dead",
    ]
    assert docker_calls[1] == ["docker", "rm", "-f", "c1", "c2"]
    assert "TIMEOUT" in res.stderr_tail and "sb-task__dead" in res.stderr_tail
    assert res.returncode == 124 and "error" in res.metrics


def test_bench_exiting_124_by_itself_is_not_our_timeout(tmp_path, monkeypatch):
    task = load_task(_task(tmp_path))
    bench = _fake_bench(monkeypatch, _RESULT, returncode=124)
    res = create_harness("benchflow:oracle", runner=bench).run(task, output_dir=tmp_path / "o")
    assert bench.docker_calls() == []  # no cleanup without our kill
    assert res.returncode == 124 and "timed_out" not in res.metrics and res.reward == 1.0


def test_cleanup_docker_calls_are_bounded(tmp_path, monkeypatch):
    """A hanging `docker ps` must not hang the cell after bench was killed."""
    import dokimi.harnesses.benchflow as mod

    for name in ("t__1", "t__2"):
        (tmp_path / "job" / name).mkdir(parents=True)

    def hanging(call):
        if call.argv[1] == "ps" and "t__1" in call.argv[-1]:
            return reply(timed_out=True)
        return reply("c9\n" if call.argv[1] == "ps" else "")

    fake = FakeRunner(hanging)
    assert mod.remove_rollout_containers(tmp_path, runner=fake) == ["t__2"]
    assert [(c.argv[1], c.timeout) for c in fake.calls] == [("ps", 10.0), ("ps", 10.0), ("rm", 60.0)]


def test_a_docker_ps_that_times_out_removes_nothing(tmp_path):
    """A partial `docker ps -q` line is an id prefix, and `docker rm -f` matches prefixes."""
    import dokimi.harnesses.benchflow as mod

    (tmp_path / "job" / "t__1").mkdir(parents=True)
    fake = FakeRunner(lambda call: reply("3f2", timed_out=True) if call.argv[1] == "ps" else reply())
    assert mod.remove_rollout_containers(tmp_path, runner=fake) == []
    assert [c.argv[1] for c in fake.calls] == ["ps"]


@pytest.fixture
def reap():
    """Kill, at teardown, the process groups whose pidfiles a test registers: the
    children below survive their parent by design, and an interrupted run (Ctrl-C,
    a timeout) would otherwise leave them behind."""
    import signal

    pidfiles: list[Path] = []
    yield pidfiles.append
    for pidfile in pidfiles:
        try:
            pid = int(pidfile.read_text())
        except (OSError, ValueError):
            continue
        for kill in (os.killpg, os.kill):
            try:
                kill(pid, signal.SIGKILL)
            except OSError:
                pass


@pytest.mark.skipif(not hasattr(os, "killpg"), reason="POSIX process groups")
def test_run_bench_kills_the_process_group_on_timeout(tmp_path, reap):
    """A real child that outlives its parent is gone after the group kill."""
    import dokimi.harnesses.benchflow as mod

    marker = tmp_path / "child.pid"
    reap(marker)
    script = f"sleep 30 & echo $! > {marker}; wait"
    run = mod._run_bench(["bash", "-c", script], 1.0, dict(os.environ))
    assert run.returncode == mod._TIMEOUT_RC and run.timed_out
    pid = int(marker.read_text())
    import time
    for _ in range(50):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.1)
    else:
        pytest.fail("child of the timed-out bench process is still alive")


@pytest.mark.skipif(not hasattr(os, "killpg"), reason="POSIX process groups")
def test_run_bench_sends_sigterm_first(tmp_path):
    """bench gets a SIGTERM it can act on before the SIGKILL."""
    import dokimi.harnesses.benchflow as mod

    marker = tmp_path / "term"
    script = f"trap 'echo got > {marker}; exit 0' TERM; sleep 60 & wait"
    run = mod._run_bench(["bash", "-c", script], 1.0, dict(os.environ))
    assert run.timed_out and marker.read_text().strip() == "got"


@pytest.mark.skipif(not hasattr(os, "killpg"), reason="POSIX process groups")
def test_run_bench_kills_a_bench_that_ignores_sigterm(tmp_path, monkeypatch, reap):
    import time

    import dokimi.harnesses.benchflow as mod

    monkeypatch.setattr(mod, "_KILL_GRACE_SEC", 1.0)
    pidfile = tmp_path / "pid"
    reap(pidfile)
    # bounded, so even a run killed before teardown leaves nothing behind for long
    script = f"trap '' TERM; echo $$ > {pidfile}; while [ $SECONDS -lt 30 ]; do sleep 0.1; done"
    start = time.monotonic()
    run = mod._run_bench(["bash", "-c", script], 1.0, dict(os.environ))
    assert run.timed_out and time.monotonic() - start < 10
    with pytest.raises(ProcessLookupError):
        os.kill(int(pidfile.read_text()), 0)


@pytest.mark.parametrize(
    "event",
    [
        {**_LOAD_SKILL, "status": "failed", "raw_input": {"skill": "nonexistent"}},
        {**_LOAD_SKILL, "kind": "read"},
        {**_LOAD_SKILL, "raw_output": "Unknown skill: nonexistent"},
    ],
)
def test_skill_loads_ignores_calls_that_loaded_nothing(tmp_path, event):
    _trajectory(tmp_path, [event])
    assert skill_loads(tmp_path, reported=0,
                       treatment=frozenset({"uxl-onetbb", "nonexistent"}))["n_skill_invocations"] == 0


def test_skill_creator_load_is_setup_not_task_use(tmp_path):
    creator = {**_LOAD_SKILL, "tool_call_id": "c1", "raw_input": {"skill": "skill-creator"},
               "raw_output": "Launching skill: skill-creator"}
    _trajectory(tmp_path, [creator, _LOAD_SKILL])
    out = skill_loads(tmp_path, reported=0, treatment=frozenset({"uxl-onetbb"}))
    assert out["skills_loaded"] == ["skill-creator", "uxl-onetbb"]
    assert out["n_skill_invocations"] == 1


def test_incomplete_scoring_has_no_reward():
    reward, m = parse_result({**_RESULT, "rewards": {"reward": 0.0},
                              "scoring": {"status": "incomplete"}})
    assert reward is None and m["error"] == "benchflow: scoring incomplete"
    reward, _ = parse_result({**_RESULT, "scoring": {"status": "complete"}})
    assert reward == 1.0


def test_bool_reward_is_not_a_reward():
    reward, m = parse_result({**_RESULT, "rewards": {"reward": True}})
    assert reward is None


def test_compose_project_name_matches_benchflow():
    from dokimi.harnesses.benchflow import _compose_project_name

    assert _compose_project_name("Foo.Bar__1a2b3c4d") == "foo-bar__1a2b3c4d"
    assert _compose_project_name("_x__1") == "0_x__1"


# ── one attempt per cell; when a cell is a verdict ────────────────────────────


def _rollout(jobs: Path, name: str, result: dict, events: list[dict] | None = None) -> Path:
    rollout = jobs / "job" / name
    rollout.mkdir(parents=True)
    (rollout / "result.json").write_text(json.dumps(result), encoding="utf-8")
    if events is not None:
        _trajectory(rollout, events)
    return rollout


def _bench_writing(*rollouts, returncode: int = 0, timed_out: bool = False) -> _Bench:
    def write(cmd, cwd):
        jobs = Path(cmd[cmd.index("--jobs-dir") + 1])
        for i, (result, events) in enumerate(rollouts):
            p = _rollout(jobs, f"sb-task__{i}", result, events)
            os.utime(p / "result.json", (1000 + i, 1000 + i))
        return returncode, "", "", timed_out

    return _Bench(write)  # docker finds no leftover containers


_IDLE = {**_RESULT, "rewards": {"reward": 0.0}, "error": "Agent idle for 60s",
         "error_category": "idle_timeout", "partial_trajectory": True,
         "agent_result": {**_RESULT["agent_result"], "total_tokens": 2610}}
_BASH = {"type": "tool_call", "tool_call_id": "b1", "kind": "execute", "status": "completed"}


def test_outer_kill_is_an_error_even_with_an_attempt_on_disk(tmp_path, monkeypatch):
    """Review item 1: rc 124 must not read a discarded attempt as a verdict."""
    task = load_task(_task(tmp_path))
    bench = _bench_writing((_IDLE, [_BASH]), returncode=124, timed_out=True)
    res = create_harness("benchflow:claude-agent-acp", runner=bench).run(task, model="m", output_dir=tmp_path / "o")
    assert res.reward is None and res.metrics["timed_out"] is True
    assert res.metrics["error"].startswith("benchflow: bench killed after")


def test_n_attempts_counts_a_rollout_that_wrote_no_result(tmp_path, monkeypatch):
    """Measured: after our kill or a hard deadline the rollout dir exists, result.json not."""
    task = load_task(_task(tmp_path))

    def dead(cmd, cwd):
        (Path(cmd[cmd.index("--jobs-dir") + 1]) / "job" / "sb-task__dead" / "agent").mkdir(parents=True)
        return 1, "", "[HARD-DEADLINE] sb-task: rollout exceeded host deadline (60s)\n"

    res = create_harness("benchflow:claude-agent-acp", runner=_Bench(dead)).run(task, model="m", output_dir=tmp_path / "o")
    assert res.metrics["n_attempts"] == 1 and "HARD-DEADLINE" in res.metrics["error"]


def test_more_than_one_attempt_is_an_error(tmp_path, monkeypatch):
    task = load_task(_task(tmp_path))
    bench = _bench_writing((_IDLE, [_BASH]), (_RESULT, [_BASH]))
    res = create_harness("benchflow:claude-agent-acp", runner=bench).run(task, model="m", output_dir=tmp_path / "o")
    assert res.metrics["n_attempts"] == 2 and res.reward is None
    assert "2 attempts" in res.metrics["error"]


def test_agent_timeout_after_an_attempt_is_a_zero(tmp_path, monkeypatch):
    task = load_task(_task(tmp_path))
    bench = _bench_writing((_IDLE, [_BASH]))
    res = create_harness("benchflow:claude-agent-acp", runner=bench).run(task, model="m", output_dir=tmp_path / "o")
    assert res.reward == 0.0 and "error" not in res.metrics
    assert res.metrics["partial_trajectory"] is True


def test_agent_timeout_with_no_model_response_is_an_error(tmp_path, monkeypatch):
    """Review item 4: a provider that never answers is not the arm's 0."""
    task = load_task(_task(tmp_path))
    dead = {**_IDLE, "error_category": "timeout", "n_tool_calls": 0,
            "agent_result": {"total_tokens": 0, "n_output_tokens": 0, "usage_source": "unavailable"}}
    bench = _bench_writing((dead, [{"type": "user_message", "text": "Do it."}]))
    res = create_harness("benchflow:claude-agent-acp", runner=bench).run(task, model="m", output_dir=tmp_path / "o")
    assert res.reward is None and "no attempt" in res.metrics["error"]


def test_an_oracle_that_overruns_its_budget_is_named_as_such(tmp_path, monkeypatch):
    """What 0.7.8 writes (measured): an exec timeout, infra_failure, no verifier run."""
    task = load_task(_task(tmp_path))
    killed = {**_RESULT, "agent": "oracle", "rewards": None, "n_tool_calls": 0, "agent_result": {},
              "error": "RuntimeError: Command timed out after 600 seconds",
              "error_category": "infra_failure"}
    bench = _bench_writing((killed, None))
    res = create_harness("benchflow:oracle", runner=bench).run(task, output_dir=tmp_path / "o")
    assert res.reward is None
    assert res.metrics["error"] == ("benchflow: oracle solve.sh exceeded agent.timeout_sec (600s); "
                                    "BenchFlow killed it and did not verify")


def test_another_oracle_exec_timeout_keeps_its_own_error(tmp_path, monkeypatch):
    """Only the exec at agent.timeout_sec is solve.sh; e.g. a 10 s setup hook is not."""
    task = load_task(_task(tmp_path))
    hook = {**_RESULT, "agent": "oracle", "rewards": None, "n_tool_calls": 0, "agent_result": {},
            "error": "RuntimeError: Command timed out after 10 seconds", "error_category": "infra_failure"}
    bench = _bench_writing((hook, None))
    res = create_harness("benchflow:oracle", runner=bench).run(task, output_dir=tmp_path / "o")
    assert res.metrics["error"] == (
        "benchflow: infra_failure: RuntimeError: Command timed out after 10 seconds")


def test_verifier_timeout_after_an_attempt_is_a_zero(tmp_path, monkeypatch):
    """As for docker-*: a verifier timeout is a failure, not missing data."""
    task = load_task(_task(tmp_path))
    vt = {**_RESULT, "rewards": None, "verifier_error": "verifier timed out after 20.0s",
          "verifier_error_category": "verifier_timeout"}
    bench = _bench_writing((vt, [_BASH]))
    res = create_harness("benchflow:claude-agent-acp", runner=bench).run(task, model="m", output_dir=tmp_path / "o")
    assert res.reward == 0.0 and res.metrics["verifier_timeout"] is True
    assert "error" not in res.metrics


def test_task_without_agent_budget_gets_600s_not_zero(tmp_path):
    """Review item 3: BenchFlow would give an ACP agent int(None or 0) = 0 s."""
    path = _task(tmp_path)
    (path / "task.md").write_text("---\nschema_version: '1.3'\n---\nDo it.\n", encoding="utf-8")
    task = load_task(path)
    cmd = create_harness("benchflow:claude-agent-acp").build_command(task, model="m", jobs_dir=tmp_path)
    override = json.loads(cmd[cmd.index("--config-override") + 1])
    assert override == {"agent": {"timeout_sec": 600}}
    oracle = create_harness("benchflow:oracle").build_command(task, model=None, jobs_dir=tmp_path)
    assert "--config-override" not in oracle


def test_outer_budget_covers_verifier_build_install_and_scenes(tmp_path):
    """Review item 2: a long verifier must not be killed by our budget."""
    from dokimi.harnesses.benchflow import outer_budget

    path = _task(tmp_path)
    (path / "task.md").write_text(
        "---\nschema_version: '1.3'\nagent:\n  timeout_sec: 5\nverifier:\n  timeout_sec: 1500\n"
        "sandbox:\n  build_timeout_sec: 1200\n---\nDo it.\n", encoding="utf-8")
    task = load_task(path)
    assert outer_budget(task, "claude-agent-acp", "no-skill") == 5 + 1500 + 1200 + 900 + 1800 + 600
    assert outer_budget(task, "claude-agent-acp", "self-gen") == 10 + 1500 + 1200 + 900 + 1800 + 600
    assert outer_budget(task, "oracle", "no-skill") == 5 + 1500 + 1200 + 1800 + 600


def test_hard_deadline_follows_benchflows_own_rules(tmp_path):
    """BenchFlow re-reads an undeclared agent budget as 900 s (not our 600 s override),
    and mimo installs for 1200 s: below that our kill fired before BenchFlow's."""
    from dokimi.harnesses.benchflow import hard_deadline

    path = _task(tmp_path)
    (path / "task.md").write_text("---\nschema_version: '1.3'\n---\nDo it.\n", encoding="utf-8")
    task = load_task(path)
    assert hard_deadline(task, "claude-agent-acp", "no-skill") == 900 + 600 + 600 + 900 + 1800
    assert hard_deadline(task, "claude-agent-acp", "self-gen") == 1800 + 600 + 600 + 900 + 1800
    assert hard_deadline(task, "mimo", "self-gen") == 1800 + 600 + 600 + 1200 + 1800


def test_benchflows_deadline_is_set_so_it_fires_before_our_kill(tmp_path, monkeypatch):
    task = load_task(_task(tmp_path))  # agent 600, verifier and build undeclared
    seen = _fake_bench(monkeypatch, _RESULT)
    create_harness("benchflow:claude-agent-acp", runner=seen).run(task, model="m", output_dir=tmp_path / "o")
    assert seen["env"]["BENCHFLOW_ROLLOUT_HARD_DEADLINE"] == "4500"
    assert seen["timeout"] == 4500 + 600
    monkeypatch.setenv("BENCHFLOW_ROLLOUT_HARD_DEADLINE", "7000")  # an exported one is kept
    create_harness("benchflow:claude-agent-acp", runner=seen).run(task, model="m", output_dir=tmp_path / "o2")
    assert seen["env"]["BENCHFLOW_ROLLOUT_HARD_DEADLINE"] == "7000" and seen["timeout"] == 7600
    monkeypatch.delenv("BENCHFLOW_ROLLOUT_HARD_DEADLINE")
    create_harness("benchflow:claude-agent-acp", runner=seen).run(task, model="m", output_dir=tmp_path / "o3",
                                                      timeout_sec=30)
    assert "BENCHFLOW_ROLLOUT_HARD_DEADLINE" not in seen["env"] and seen["timeout"] == 30


def test_bench_runs_outside_the_callers_cwd(tmp_path, monkeypatch):
    """Review item 10: bench reads ./.env ahead of the exported env."""
    seen = _fake_bench(monkeypatch, _RESULT)
    create_harness("benchflow:claude-agent-acp", runner=seen).run(load_task(_task(tmp_path)),
                                                                  output_dir=tmp_path / "o")
    assert seen["cwd"] == tmp_path / "o" and seen["cmd"]


def test_preflight_names_the_missing_compose_plugin(monkeypatch):
    fake = FakeRunner(lambda call: reply(stderr="docker: unknown command: docker compose",
                                         returncode=1 if call.argv[:2] == ("docker", "compose") else 0))
    with pytest.raises(PreflightError, match="Compose v2 plugin"):
        BenchflowHarness("h", "a", runner=fake).preflight()


def test_sweep_stops_once_when_preflight_fails(tmp_path, monkeypatch):
    bench = _Bench(lambda cmd, cwd: pytest.fail("bench must not run"),
                   docker=lambda argv: reply(returncode=1))  # no Compose v2
    runner = TaskSuiteRunner(
        tasks=[load_task(_task(tmp_path))],
        harnesses=["benchflow:oracle", "benchflow-skill:oracle"],
        output_root=tmp_path / "runs",
        runner=bench,
    )
    with pytest.raises(PreflightError, match="docker compose version"):
        runner.run()
    assert [c.argv[:2] for c in bench.calls] == [("bench", "--version"), ("docker", "compose")]


def test_dry_run_skips_the_preflight(tmp_path, monkeypatch):
    nothing = FakeRunner(lambda call: pytest.fail(f"a dry run ran {call.argv}"))
    TaskSuiteRunner(tasks=[load_task(_task(tmp_path))], harnesses=["benchflow:oracle"],
                    output_root=tmp_path / "runs", dry_run=True, runner=nothing).run()


def test_cli_prints_a_failed_preflight_as_one_line(tmp_path, monkeypatch):
    from dokimi.cli import build_parser
    import dokimi.harnesses.benchflow as mod

    def failing():
        raise PreflightError("benchflow preflight: `docker compose version` exited 1; install it")

    monkeypatch.setattr(mod.BenchflowHarness, "preflight", staticmethod(failing))
    _task(tmp_path)
    args = build_parser().parse_args([
        "tasks", "run", "--tasks", "sb-task", "--tasks-root", str(tmp_path),
        "--harnesses", "benchflow:oracle", "--output-dir", str(tmp_path / "runs"),
    ])
    with pytest.raises(SystemExit) as exc:
        args.func(args)
    assert str(exc.value) == (
        "error: benchflow preflight: `docker compose version` exited 1; install it"
    )


def _cwd_resolving_bench(result) -> _Bench:
    """A bench that, like the real one, resolves every path against the cwd it is given."""

    def write(cmd, cwd):
        jobs = Path(cwd) / cmd[cmd.index("--jobs-dir") + 1]
        rollout = jobs / "2026-10-03__20-00-00" / "sb-task__abcd"
        rollout.mkdir(parents=True)
        (rollout / "result.json").write_text(json.dumps(result), encoding="utf-8")
        return 0, "", ""

    return _Bench(write)


def test_relative_output_dir_still_finds_the_rollout(tmp_path, monkeypatch):
    """The default --output-dir is relative; bench runs in the cell, so the jobs dir must be absolute."""
    task = load_task(_task(tmp_path))
    monkeypatch.chdir(tmp_path)
    seen = _cwd_resolving_bench(_RESULT)
    res = create_harness("benchflow:claude-agent-acp", runner=seen).run(task, output_dir=Path("results/cell"))
    assert Path(seen["cmd"][seen["cmd"].index("--jobs-dir") + 1]).is_absolute()
    assert res.reward == 1.0 and "error" not in res.metrics
    assert res.metrics["n_attempts"] == 1
    # the row records the dir as given, like a docker-* row; only bench sees it absolute
    assert res.output_dir == "results/cell"
    assert res.metrics["result_json"] == "results/cell/jobs/2026-10-03__20-00-00/sb-task__abcd/result.json"


def test_relative_skill_paths_reach_bench_absolute(tmp_path, monkeypatch):
    task = load_task(_task(tmp_path))
    ext = tmp_path / "ext" / "my-skill"
    ext.mkdir(parents=True)
    (ext / "SKILL.md").write_text("---\nname: my-skill\n---\nx\n", encoding="utf-8")
    creator = tmp_path / "creators" / "skill-creator"
    creator.mkdir(parents=True)
    (creator / "SKILL.md").write_text("---\nname: skill-creator\n---\nx\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    seen = _cwd_resolving_bench(_RESULT)
    create_harness("benchflow-skill:claude-agent-acp:ext", runner=seen).run(task, output_dir=Path("o1"))
    assert Path(seen["cmd"][seen["cmd"].index("--skills-dir") + 1]).is_absolute()
    create_harness("benchflow-selfgen:claude-agent-acp:creators", runner=seen).run(task, output_dir=Path("o2"))
    staged = Path(seen["cmd"][seen["cmd"].index("--skill-creator-dir") + 1])
    assert staged.is_absolute() and staged == (tmp_path / "o2" / "creator" / "skill-creator")
    assert (staged / "SKILL.md").is_file()


def test_bench_env_is_wide_and_oracle_skips_the_agents_clone(monkeypatch):
    monkeypatch.delenv("COLUMNS", raising=False)
    monkeypatch.delenv("BENCHFLOW_AGENTS_SOURCE", raising=False)
    env = _subprocess_env({}, "oracle")
    assert env["COLUMNS"] == "400" and env["BENCHFLOW_AGENTS_SOURCE"] == "off"
    env = _subprocess_env({}, "claude-agent-acp")
    assert "BENCHFLOW_AGENTS_SOURCE" not in env
    env = _subprocess_env({"BENCHFLOW_AGENTS_SOURCE": "x/y@z", "COLUMNS": "80"}, "oracle")
    assert env["BENCHFLOW_AGENTS_SOURCE"] == "x/y@z"
    assert env["COLUMNS"] == "400"  # an exported narrow terminal would wrap the reason again


def test_path_env_vars_are_anchored_at_the_callers_cwd(tmp_path, monkeypatch):
    """bench runs in the cell: a relative creator dir or Docker config would miss."""
    (tmp_path / "agents").mkdir()
    monkeypatch.chdir(tmp_path)
    env = _subprocess_env({"BENCHFLOW_SKILL_CREATOR_DIR": "creators/c", "DOCKER_CONFIG": "dockercfg",
                           "BENCHFLOW_AGENTS_SOURCE": "agents"}, "claude-agent-acp")
    assert env["BENCHFLOW_SKILL_CREATOR_DIR"] == str(tmp_path / "creators" / "c")
    assert env["DOCKER_CONFIG"] == str(tmp_path / "dockercfg")
    assert env["BENCHFLOW_AGENTS_SOURCE"] == str(tmp_path / "agents")
    # an owner/repo@ref spec, or an absolute path, is left alone
    env = _subprocess_env({"BENCHFLOW_AGENTS_SOURCE": "benchflow-ai/agents@main",
                           "DOCKER_CONFIG": "/etc/docker"}, "claude-agent-acp")
    assert env["BENCHFLOW_AGENTS_SOURCE"] == "benchflow-ai/agents@main"
    assert env["DOCKER_CONFIG"] == "/etc/docker"


def test_error_line_skips_benchflows_agent_registry_banner(tmp_path, monkeypatch):
    """Real 0.7.8 stderr for an agent bench fetches from benchflow-ai/agents (goose):
    the banner comes before the reason. (An `oracle` cell no longer clones.)"""
    stderr = (
        "Cloning benchflow-ai/agents from https://github.com/benchflow-ai/agents.git ...\n"
        "Agent manifest auto-load: registered 28 agent(s) from benchflow-ai/agents@main: amp-acp\n"
        "/t/task.md: task.md parse error: 1 validation error for TaskConfig\n"
    )
    res = create_harness("benchflow:goose", runner=_Bench(lambda cmd, cwd: (1, "", stderr))).run(
        load_task(_task(tmp_path)), output_dir=tmp_path / "o")
    assert res.metrics["error"] == (
        "no BenchFlow result.json (exit 1): /t/task.md: task.md parse error: 1 validation error for TaskConfig")


# Real 0.7.8 stderr once the job has started: progress lines first, the reason last.
_HARD_DEADLINE_STDERR = (
    "Job: 1 tasks, 0 done, 1 to run (concurrency=4)\n"
    "Starting environment: sb-task\n"
    "Setting up sandbox user: agent\n"
    "Sandbox user agent ready (workspace=/app)\n"
    "Oracle mode: running oracle solve.sh\n"
    "[HARD-DEADLINE] sb-task: rollout exceeded host deadline (60s); abandoning sandbox\n"
    "[ERR] sb-task (tools=0) (Rollout exceeded host hard deadline (60s) —…)\n"
    "Job complete: 0/1 (0.0%), errors=1, idle_timeouts=0, time=1.1min\n"
)
_ERR_STDERR = (
    "Job: 1 tasks, 0 done, 1 to run (concurrency=4)\n"
    "[ERR] sb-task: unexpected exception: Could not find skill-creator. Pass "
    "--skill-creator-dir or set BENCHFLOW_SKILL_CREATOR_DIR.\n"
    "Job complete: 0/1 (0.0%), errors=1, idle_timeouts=0, time=0.0min\n"
)


@pytest.mark.parametrize("stderr, reason", [
    (_HARD_DEADLINE_STDERR,
     "[HARD-DEADLINE] sb-task: rollout exceeded host deadline (60s); abandoning sandbox"),
    (_ERR_STDERR, "[ERR] sb-task: unexpected exception: Could not find skill-creator. Pass "
                  "--skill-creator-dir or set BENCHFLOW_SKILL_CREATOR_DIR."),
])
def test_error_line_names_the_reason_after_the_job_started(tmp_path, monkeypatch, stderr, reason):
    """Not the `Job: ...` progress line that opens every job's stderr."""
    bench = _Bench(lambda cmd, cwd: (1, "", stderr))
    res = create_harness("benchflow:oracle", runner=bench).run(load_task(_task(tmp_path)), output_dir=tmp_path / "o")
    assert res.metrics["error"] == f"no BenchFlow result.json (exit 1): {reason}"


@pytest.mark.parametrize("banner", ["Cloning benchflow-ai/agents from https://x ...",
                                    "Refreshing benchflow-ai/agents at main"])
def test_error_line_skips_either_registry_banner(banner):
    from dokimi.harnesses.benchflow import _error_line

    assert _error_line(f"{banner}\nUnknown agent 'zz' — not in registry\n") == (
        "Unknown agent 'zz' — not in registry")


def test_synthetic_api_error_text_is_not_an_attempt(tmp_path, monkeypatch):
    """claude-agent-acp 0.73.0 forwards 'API Error: ...' as an agent_message; a timeout
    after only that is a provider failure, not a 0."""
    task = load_task(_task(tmp_path))
    dead = {**_IDLE, "error_category": "timeout", "n_tool_calls": 0,
            "agent_result": {"total_tokens": 0, "n_output_tokens": 0, "usage_source": "unavailable"}}
    api_error = {"type": "agent_message", "text": "API Error: Request rejected (429) \u00b7 slow down"}
    bench = _bench_writing((dead, [{"type": "user_message", "text": "Do it."}, api_error]))
    res = create_harness("benchflow:claude-agent-acp", runner=bench).run(task, model="m", output_dir=tmp_path / "o")
    assert res.reward is None and "no attempt" in res.metrics["error"]
    # a real reply after the error is an attempt
    bench = _bench_writing((dead, [api_error, {"type": "agent_message", "text": "Let me look."}]))
    res = create_harness("benchflow:claude-agent-acp", runner=bench).run(task, model="m", output_dir=tmp_path / "o2")
    assert res.reward == 0.0 and "error" not in res.metrics


@pytest.mark.parametrize("text", [
    "Failed to authenticate. API Error: 403 {\"type\":\"error\",\"error\":{\"type\":\"permission_error\"}}",
    "Credit balance is too low",
    "Invalid API key \u00b7 Fix external API key",
    "**Auto mode unavailable:** the model does not support it",
])
def test_other_synthetic_agent_messages_are_not_an_attempt(tmp_path, monkeypatch, text):
    """Measured for 403 and a 400 credit error: claude-agent-acp forwards these as agent
    messages with no model reply; a timeout after only that is not the arm's 0."""
    task = load_task(_task(tmp_path))
    dead = {**_IDLE, "error_category": "timeout", "n_tool_calls": 0,
            "agent_result": {"total_tokens": 0, "n_output_tokens": 0, "usage_source": "unavailable"}}
    bench = _bench_writing((dead, [{"type": "user_message", "text": "Do it."},
                                        {"type": "agent_message", "text": text}]))
    res = create_harness("benchflow:claude-agent-acp", runner=bench).run(task, model="m", output_dir=tmp_path / "o")
    assert res.reward is None and "no attempt" in res.metrics["error"]


def test_skill_loads_trusts_benchflows_skill_kind_for_other_agents(tmp_path):
    """gemini's activate_skill / opencode's skill: kind 'skill', a plain-string rawOutput."""
    _trajectory(tmp_path, [{
        "type": "tool_call", "tool_call_id": "g1", "kind": "skill", "title": "activate_skill",
        "status": "completed", "raw_input": {"name": "dialogue-graph"},
        "raw_output": "Skill dialogue-graph activated",
    }])
    out = skill_loads(tmp_path, reported=1, treatment=frozenset({"dialogue-graph"}))
    assert out["skills_loaded"] == ["dialogue-graph"] and out["n_skill_invocations"] == 1


def test_skill_loads_ignores_an_unrelated_tool_with_a_name_input(tmp_path):
    _trajectory(tmp_path, [{
        "type": "tool_call", "tool_call_id": "m1", "kind": "other", "title": "mcp__x__lookup",
        "status": "completed", "raw_input": {"name": "dialogue-graph"}, "raw_output": None,
    }])
    out = skill_loads(tmp_path, reported=0, treatment=frozenset({"dialogue-graph"}))
    assert out["skills_loaded"] == [] and out["n_skill_invocations"] == 0


def test_run_bench_normal_exit_runs_in_the_cell(tmp_path):
    """The real Popen path: cwd is honoured, a normal exit is not a timeout, bad bytes are replaced."""
    import sys

    import dokimi.harnesses.benchflow as mod

    code = "import os,sys; print(os.getcwd(), flush=True); sys.stdout.buffer.write(bytes([255])); sys.exit(3)"
    out = mod._run_bench([sys.executable, "-c", code], 30, dict(os.environ), cwd=tmp_path)
    assert out.returncode == 3 and out.timed_out is False
    assert out.stdout.splitlines()[0] == str(tmp_path.resolve()) and "\ufffd" in out.stdout


@pytest.mark.parametrize("category", ["timeout", "idle_timeout"])
def test_either_timeout_with_no_model_response_is_an_error(tmp_path, monkeypatch, category):
    task = load_task(_task(tmp_path))
    dead = {**_IDLE, "error_category": category, "n_tool_calls": 0,
            "agent_result": {"total_tokens": 0, "n_output_tokens": 0, "usage_source": "unavailable"}}
    bench = _bench_writing((dead, [{"type": "user_message", "text": "Do it."}]))
    res = create_harness("benchflow:claude-agent-acp", runner=bench).run(task, model="m", output_dir=tmp_path / "o")
    assert res.reward is None and "no attempt" in res.metrics["error"]


def test_a_declared_agent_budget_is_not_overridden(tmp_path):
    path = _task(tmp_path)
    (path / "task.md").write_text(
        "---\nschema_version: '1.3'\nagent:\n  timeout_sec: 120\n---\nDo it.\n", encoding="utf-8")
    cmd = create_harness("benchflow:claude-agent-acp").build_command(
        load_task(path), model="m", jobs_dir=tmp_path)
    assert "--config-override" not in cmd


@pytest.mark.parametrize("failing, match", [
    (FileNotFoundError("bench"), "benchflow preflight: `bench --version` failed"),
    (None, "`bench --version` exited 2"),
])
def test_preflight_names_a_missing_or_broken_bench(monkeypatch, failing, match):
    def fake(call):
        if failing is not None:
            raise failing
        return reply(stderr="boom", returncode=2)

    with pytest.raises(PreflightError, match=match):
        BenchflowHarness("h", "a", runner=FakeRunner(fake)).preflight()


@pytest.mark.parametrize("category", ["timeout", "idle_timeout"])
def test_a_late_agent_timeout_scores_zero_and_keeps_the_verifier_reward(tmp_path, monkeypatch, category):
    """BenchFlow runs the verifier after an agent timeout; docker-* scores the timeout 0."""
    task = load_task(_task(tmp_path))
    late = {**_IDLE, "error_category": category, "rewards": {"reward": 1.0}}
    bench = _bench_writing((late, [_BASH]))
    res = create_harness("benchflow:claude-agent-acp", runner=bench).run(task, model="m", output_dir=tmp_path / "o")
    assert res.reward == 0.0 and res.passed is False and "error" not in res.metrics
    assert res.metrics["reward_after_timeout"] == 1.0


def test_a_timeout_that_scored_zero_records_no_reward_after_timeout(tmp_path, monkeypatch):
    task = load_task(_task(tmp_path))
    bench = _bench_writing((_IDLE, [_BASH]))
    res = create_harness("benchflow:claude-agent-acp", runner=bench).run(task, model="m", output_dir=tmp_path / "o")
    assert res.reward == 0.0 and "reward_after_timeout" not in res.metrics


def test_a_late_timeout_with_partial_credit_is_still_zero(tmp_path, monkeypatch):
    task = load_task(_task(tmp_path))
    bench = _bench_writing(({**_IDLE, "error_category": "timeout", "rewards": {"reward": 0.5}}, [_BASH]))
    res = create_harness("benchflow:claude-agent-acp", runner=bench).run(task, model="m", output_dir=tmp_path / "o")
    assert res.reward == 0.0 and res.metrics["reward_after_timeout"] == 0.5


@pytest.mark.parametrize("after", [
    {"rewards": None, "verifier_error": "verifier crashed: no reward file found",
     "verifier_error_category": "verifier_dep_install"},
    {"scoring": {"status": "incomplete"}},
])
def test_a_late_timeout_is_zero_whatever_the_verifier_did(tmp_path, monkeypatch, after):
    """Measured: solved, then the model reply hung; the verifier then crashed. docker-*
    scores a solve timeout 0 without verifying, so the verifier cannot turn it into an error."""
    task = load_task(_task(tmp_path))
    late = {**_IDLE, "error_category": "timeout",
            "error": "Agent prompt exceeded wall-clock budget 90s", **after}
    bench = _bench_writing((late, [_BASH]))
    res = create_harness("benchflow:claude-agent-acp", runner=bench).run(task, model="m", output_dir=tmp_path / "o")
    assert res.reward == 0.0 and "error" not in res.metrics
    assert res.metrics["benchflow_error_category"] == "timeout"
    if "verifier_error" in after:
        assert res.metrics["verifier_error"].startswith("verifier crashed")


def test_a_no_attempt_timeout_keeps_the_figure_it_drops(tmp_path, monkeypatch):
    task = load_task(_task(tmp_path))
    dead = {**_IDLE, "error_category": "timeout", "rewards": {"reward": 1.0}, "n_tool_calls": 0,
            "agent_result": {"total_tokens": 0, "n_output_tokens": 0, "usage_source": "unavailable"}}
    bench = _bench_writing((dead, [{"type": "user_message", "text": "Do it."}]))
    res = create_harness("benchflow:claude-agent-acp", runner=bench).run(task, model="m", output_dir=tmp_path / "o")
    assert res.reward is None and "no attempt" in res.metrics["error"]
    assert res.metrics["reward_after_timeout"] == 1.0


def test_every_pack_in_the_deployed_root_is_treatment(tmp_path):
    """Documented: a distractor pack placed in the root counts as a treatment load."""
    from dokimi.harnesses.benchflow import treatment_names

    root = tmp_path / "skills"
    for name in ("uxl-onetbb", "distractor-x"):
        (root / name).mkdir(parents=True)
        (root / name / "SKILL.md").write_text(f"---\nname: {name}\n---\nx\n", encoding="utf-8")
    rollout = tmp_path / "rollout"
    _trajectory(rollout, [
        {**_LOAD_SKILL, "tool_call_id": "a", "raw_input": {"skill": "distractor-x"},
         "raw_output": "Launching skill: distractor-x"},
        {**_LOAD_SKILL, "tool_call_id": "b", "raw_input": {"skill": "not-in-the-root"},
         "raw_output": "Launching skill: not-in-the-root"},
    ])
    out = skill_loads(rollout, reported=2, treatment=treatment_names(root))
    assert out["n_skill_invocations"] == 1


def test_benchflow_oracle_is_a_control_not_an_arm(tmp_path, monkeypatch):
    """Like docker-oracle (#70): an oracle row would only enlarge the Holm family."""
    from dokimi.harnesses.runner import _is_control_harness

    assert _is_control_harness("benchflow:oracle")
    assert _is_control_harness("benchflow-skill:oracle:/x/skills")
    assert _is_control_harness("benchflow-selfgen:oracle")
    assert not _is_control_harness("benchflow:claude-agent-acp")
    assert not _is_control_harness("benchflow-skill:acp:pi")
    task = load_task(_task(tmp_path))
    bench = _fake_bench(monkeypatch, _RESULT)
    report = TaskSuiteRunner(
        tasks=[task],
        harnesses=["benchflow:claude-agent-acp", "benchflow-skill:claude-agent-acp", "benchflow:oracle"],
        baseline_harness="benchflow:claude-agent-acp",
        output_root=tmp_path / "runs",
        runner=bench,
    ).run()
    assert list(report["summary"]["comparisons"]) == ["benchflow-skill:claude-agent-acp"]


def test_an_arm_without_token_data_sums_to_unknown_not_zero(tmp_path, monkeypatch):
    """Rows with usage_source unavailable carry no token keys; the arm's sums are null."""
    from dokimi.report.task_runs_report import render_task_runs_report

    task = load_task(_task(tmp_path))
    dead = {**_RESULT, "agent_result": {"usage_source": "unavailable", "total_tokens": 0}}
    bench = _fake_bench(monkeypatch, dead)
    report = TaskSuiteRunner(tasks=[task], harnesses=["benchflow:claude-agent-acp"],
                             output_root=tmp_path / "runs", runner=bench).run()
    validate_artifact("task_runs", report)
    cost = report["summary"]["per_harness"]["benchflow:claude-agent-acp"]["cost"]
    assert cost["total_cost_usd"] is None
    assert cost["prompt_tokens"] is None and cost["total_tokens"] is None
    rows = [line for line in render_task_runs_report(report).splitlines()
            if line.startswith("| `benchflow:claude-agent-acp` |")]
    assert rows and not any("None" in line for line in rows)


# ── branches the cell rule, preflight and skill counting rely on ─────────────

_NO_SIGNAL = {"n_tool_calls": 0, "agent_result": {"n_output_tokens": 0, "usage_source": "unavailable"}}


def _run_one(tmp_path, monkeypatch, result, events=None, harness="benchflow:claude-agent-acp"):
    bench = _bench_writing((result, events))
    return create_harness(harness, runner=bench).run(load_task(_task(tmp_path)), model="m", output_dir=tmp_path / "o")


def test_an_oracle_cell_runs_bench_with_the_agents_clone_off(tmp_path, monkeypatch):
    seen = _fake_bench(monkeypatch, _RESULT)
    create_harness("benchflow:oracle", runner=seen).run(load_task(_task(tmp_path)), output_dir=tmp_path / "o")
    assert seen["env"]["BENCHFLOW_AGENTS_SOURCE"] == "off"


@pytest.mark.parametrize("extra, events", [
    ({"n_tool_calls": 3}, None),
    ({"agent_result": {"n_output_tokens": 50, "usage_source": "provider_response"}}, None),
    ({}, [{"type": "agent_thought", "text": "thinking"}]),
    ({}, [_BASH]),
], ids=["tool-calls", "completion-tokens", "agent-thought", "tool-call-event"])
def test_each_attempt_signal_alone_is_an_attempt(tmp_path, monkeypatch, extra, events):
    timed_out = {**_RESULT, **_NO_SIGNAL, **extra, "rewards": {"reward": 0.0},
                 "error": "Agent prompt exceeded wall-clock budget 90s", "error_category": "timeout"}
    res = _run_one(tmp_path, monkeypatch, timed_out, events)
    assert res.reward == 0.0 and "error" not in res.metrics


def test_a_verifier_timeout_without_an_attempt_is_an_error(tmp_path, monkeypatch):
    vt = {**_RESULT, **_NO_SIGNAL, "rewards": None, "verifier_error": "verifier timed out after 20.0s",
          "verifier_error_category": "verifier_timeout"}
    res = _run_one(tmp_path, monkeypatch, vt, [])
    assert res.reward is None and "verifier_timeout" in res.metrics["error"]


def test_a_verifier_infra_error_after_an_attempt_is_an_error(tmp_path, monkeypatch):
    vi = {**_RESULT, "rewards": None, "verifier_error": "docker exec failed",
          "verifier_error_category": "verifier_infra"}
    res = _run_one(tmp_path, monkeypatch, vi, [_BASH])
    assert res.reward is None and "verifier_infra" in res.metrics["error"]


def test_only_a_timeout_is_zeroed_another_error_keeps_its_reward(tmp_path, monkeypatch):
    late_crash = {**_RESULT, "error": "acp crashed after the verifier", "error_category": "acp_error"}
    res = _run_one(tmp_path, monkeypatch, late_crash, [_BASH])
    assert res.reward == 1.0 and "reward_after_timeout" not in res.metrics


def test_an_undeclared_agent_budget_is_defaulted_and_recorded(tmp_path, monkeypatch):
    path = _task(tmp_path)
    (path / "task.md").write_text("---\nschema_version: '1.3'\n---\nDo it.\n", encoding="utf-8")
    seen = _fake_bench(monkeypatch, _RESULT)
    res = create_harness("benchflow:claude-agent-acp", runner=seen).run(load_task(path), model="m",
                                                           output_dir=tmp_path / "o")
    assert res.metrics["agent_timeout_defaulted_sec"] == 600
    assert seen["timeout"] == 900 + 600 + 600 + 900 + 1800 + 600  # BenchFlow reads 900 s


def test_preflight_calls_are_bounded(monkeypatch):
    fake = FakeRunner(lambda call: reply("ok"))
    BenchflowHarness("h", "a", runner=fake).preflight()
    assert [c.timeout for c in fake.calls] == [30, 30]


def test_preflight_runs_once_per_harness_class_when_it_passes(tmp_path, monkeypatch):
    bench = _Bench(_writes_rollout(_RESULT))
    TaskSuiteRunner(tasks=[load_task(_task(tmp_path))],
                    harnesses=["benchflow:oracle", "benchflow-skill:oracle"],
                    output_root=tmp_path / "runs", runner=bench).run()
    assert [c.argv[:2] for c in bench.calls].count(("bench", "--version")) == 1


def test_subject_work_prints_a_failed_preflight_as_one_line(tmp_path, monkeypatch):
    from dokimi.cli import build_parser
    from dokimi.commands.subjects import _run_subject_work
    from dokimi.subjects import load_subject

    def failing():
        raise PreflightError("benchflow preflight: `bench --version` failed; install benchflow")

    monkeypatch.setattr(_bf.BenchflowHarness, "preflight", staticmethod(failing))
    _task(tmp_path / "tasks")
    subject = tmp_path / "subject.toml"
    subject.write_text('[subject]\nid = "s"\nkind = "skill"\nref = "/tmp/x"\n\n[suite]\n'
                       'products = ["oneTBB"]\nquestions = ["q.json"]\ntasks = ["sb-task"]\n',
                       encoding="utf-8")
    args = build_parser().parse_args(["subjects", "run", str(subject), "--work-harnesses",
                                      "benchflow:oracle", "--tasks-root", str(tmp_path / "tasks")])
    with pytest.raises(SystemExit) as exc:
        _run_subject_work(load_subject(subject), args, out_dir=tmp_path / "out")
    assert str(exc.value).startswith("error: benchflow preflight")


def test_a_pack_loaded_by_its_frontmatter_name_is_treatment(tmp_path):
    from dokimi.harnesses.benchflow import treatment_names

    (tmp_path / "skills" / "uxl_onetbb_v2").mkdir(parents=True)
    (tmp_path / "skills" / "uxl_onetbb_v2" / "SKILL.md").write_text(
        "---\nname: uxl-onetbb\n---\nx\n", encoding="utf-8")
    assert treatment_names(tmp_path / "skills") == frozenset({"uxl_onetbb_v2", "uxl-onetbb"})


@pytest.mark.parametrize("kind", [None, "", "tool"])
def test_claudes_load_shape_counts_under_every_unclassified_kind(tmp_path, kind):
    event = {**_LOAD_SKILL, "kind": kind}
    if kind is None:
        event.pop("kind")
    _trajectory(tmp_path, [event])
    assert skill_loads(tmp_path, 0, treatment=frozenset({"uxl-onetbb"}))["n_skill_invocations"] == 1


def test_unavailable_usage_drops_the_cache_tokens_too():
    _, m = parse_result({**_RESULT, "agent_result": {**_RESULT["agent_result"],
                                                     "usage_source": "unavailable"}})
    assert not {"cache_read_tokens", "cache_write_tokens", "prompt_tokens", "cost_usd"} & m.keys()


def test_a_creator_root_with_two_packs_is_refused(tmp_path):
    for name in ("a", "b"):
        (tmp_path / "c" / name).mkdir(parents=True)
        (tmp_path / "c" / name / "SKILL.md").write_text("x", encoding="utf-8")
    with pytest.raises(FileNotFoundError, match="no skill-creator SKILL.md"):
        create_harness(f"benchflow-selfgen:x:{tmp_path / 'c'}")._creator_skill_dir()
    (tmp_path / "one" / "a").mkdir(parents=True)
    (tmp_path / "one" / "a" / "SKILL.md").write_text("x", encoding="utf-8")
    assert create_harness(f"benchflow-selfgen:x:{tmp_path / 'one'}")._creator_skill_dir() == (
        tmp_path / "one" / "a")


@pytest.mark.parametrize("var", ["http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY"])
def test_each_proxy_variable_triggers_the_bypass(monkeypatch, var):
    monkeypatch.setenv(var, "http://proxy.example:912")
    env = _subprocess_env({}, runner=_gateway(None))
    assert "172.17.0.1" in env["no_proxy"].split(",") and env["no_proxy"] == env["NO_PROXY"]


def test_bridge_gateway_reads_dockers_answer():
    found = _gateway("10.9.0.1")
    assert _bf._bridge_gateway(found) == "10.9.0.1"
    assert found.calls[0].argv[:4] == ("docker", "network", "inspect", "bridge") and found.calls[0].timeout == 10
    assert _bf._bridge_gateway(_gateway(None)) is None
    assert _bf._bridge_gateway(FakeRunner(lambda call: reply(timed_out=True))) is None


def test_preflight_that_times_out_names_the_command(monkeypatch):
    fake = FakeRunner(lambda call: reply(timed_out=True))
    with pytest.raises(PreflightError, match=r"`bench --version` failed \(timed out after 30s\)"):
        BenchflowHarness("h", "a", runner=fake).preflight()


def test_cleanup_and_bridge_lookup_without_a_docker_cli(tmp_path):
    """No docker on PATH: nothing to remove and no gateway, rather than an error out of the cell."""
    (tmp_path / "job" / "t__1").mkdir(parents=True)

    def missing(call):
        raise FileNotFoundError(2, "No such file or directory", call.argv[0])

    assert _bf.remove_rollout_containers(tmp_path, runner=FakeRunner(missing)) == []
    assert _bf._bridge_gateway(FakeRunner(missing)) is None


# ── review follow-ups: env, runner and error line pinned ──────────────────────


def test_run_looks_up_the_bridge_through_the_harness_runner(tmp_path, monkeypatch):
    """Behind a proxy the bridge lookup must go through the given runner, not a real docker."""
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.example:912/")
    task = load_task(_task(tmp_path))
    bench = _fake_bench(monkeypatch, _RESULT)
    bench.docker = lambda argv: reply("10.9.0.1\n")
    create_harness("benchflow:claude-agent-acp", runner=bench).run(task, model="m", output_dir=tmp_path / "o")
    assert any(c[:3] == ["docker", "network", "inspect"] for c in bench.docker_calls())
    assert "10.9.0.1" in bench["env"]["no_proxy"].split(",")


def test_cleanup_docker_calls_inherit_the_environment_and_skip_a_timed_out_rm(tmp_path, monkeypatch):
    """DOCKER_HOST/DOCKER_CONFIG/HOME must reach docker; a hung `rm` removed nothing."""
    import dokimi.harnesses.benchflow as mod

    monkeypatch.setenv("DOCKER_HOST", "unix:///x.sock")
    for name in ("t__1", "t__2"):
        (tmp_path / "job" / name).mkdir(parents=True)

    def answer(call):
        if call.argv[1] == "ps":
            return reply("c1\n" if "t__1" in call.argv[-1] else "c2\n")
        return reply(timed_out=True) if "c1" in call.argv else reply()

    fake = FakeRunner(answer)
    assert mod.remove_rollout_containers(tmp_path, runner=fake) == ["t__2"]
    assert all(c.env.get("DOCKER_HOST") == "unix:///x.sock" for c in fake.calls)


def test_preflight_docker_and_bench_checks_inherit_the_environment(monkeypatch):
    monkeypatch.setenv("DOCKER_HOST", "unix:///x.sock")
    fake = FakeRunner(lambda call: reply("ok\n"))
    harness = create_harness("benchflow:claude-agent-acp", runner=fake)
    try:
        harness.preflight()
    except PreflightError:
        pass
    assert fake.calls and all(c.env.get("DOCKER_HOST") == "unix:///x.sock" for c in fake.calls)


def test_error_line_skips_the_capture_limit_marker():
    """Over 1 MiB of stderr, the runner's marker is not the reason bench gave."""
    from dokimi.harnesses.benchflow import _error_line
    from dokimi.process import TRUNCATION_MARKER

    marker = TRUNCATION_MARKER.format(dropped=123)
    assert _error_line(f"progress{marker}[ERR] sandbox failed\n").startswith("[ERR] sandbox failed")
    assert "bytes not kept" not in _error_line(f"{marker}")
