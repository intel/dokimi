# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Workspace preparation, containers and `repo tasks check` (contract §3, §4.3). No docker, no LLM."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from dokimi.cli import build_parser
from dokimi.repos import RepoSource, load_manifest, open_source
from dokimi.repos.container import (
    CONTENT_DIR,
    SOURCE_DIR,
    ContainerError,
    ContainerResult,
    ContainerSpec,
    DockerRunner,
    EgressNotImplemented,
    Mount,
    docker_network,
    run_process,
)
from dokimi.repos.events import convert
from dokimi.repos.grading import gate_ok, outcome, render_entry
from dokimi.repos.harness_profile import load_profile
from dokimi.repos.workspace import (
    PrepError,
    Task,
    export_build_context,
    export_eval_dir,
    load_tasks,
    prepare_workspace,
    source_git_dir,
)
from repo_eval_fixture import FakeRunner, fake_harness, git, make_repo


@pytest.fixture
def toy(tmp_path):
    root, base = make_repo(tmp_path)
    source = RepoSource.parse(f"{root}@HEAD")
    tree = open_source(source)
    manifest = load_manifest(tree, ".agent-eval/repo-eval.yaml")
    content = export_eval_dir(tree, manifest, tmp_path / "content")
    return {"root": root, "base": base, "tree": tree, "manifest": manifest, "content": content,
            "git_dir": source_git_dir(source, tree), "tmp": tmp_path}


def _tasks(toy, *ids):
    return {t.id: t for t in load_tasks(toy["content"], toy["manifest"], list(ids) or None)}


def test_prepare_applies_contract_steps_in_order(toy):
    task = _tasks(toy)["fix_add"]
    rd = toy["tmp"] / "run"
    meta = prepare_workspace(toy["git_dir"], toy["manifest"], task, "guidance:raw", rd)
    ws = rd / "workspace"
    assert not (ws / ".agent-eval").exists()                       # eval_dir deleted
    assert "a - b" in (ws / "src/calc.py").read_text()             # setup patch applied
    assert git(ws, "rev-list", "--count", "HEAD") == "1"           # no upstream history
    assert git(ws, "status", "--porcelain") == ""                  # patch amended into the snapshot
    assert meta["base_sha"] == toy["base"] and meta["base_rev"] == toy["base"]
    assert meta["start_sha"] == git(ws, "rev-parse", "HEAD") and meta["base_date"]
    assert [p["path"] for p in meta["patches"]] == ["setup.patch"] and len(meta["patches"][0]["sha256"]) == 64
    again = prepare_workspace(toy["git_dir"], toy["manifest"], task, "guidance:raw", toy["tmp"] / "run2")
    assert again["start_sha"] == meta["start_sha"]                 # repetitions start from one commit


def test_review_patch_is_the_head_commit_under_a_neutral_author(toy):
    task = _tasks(toy)["review_add"]
    meta = prepare_workspace(toy["git_dir"], toy["manifest"], task, "guidance:raw", toy["tmp"] / "r")
    ws = toy["tmp"] / "r" / "workspace"
    assert git(ws, "rev-list", "--count", "HEAD") == "2"
    assert git(ws, "log", "-1", "--format=%an <%ae>|%s") == "Contributor <contributor@example.invalid>|add mul"
    assert "def mul" in (ws / "src/calc.py").read_text()
    assert meta["start_sha"] == git(ws, "rev-parse", "HEAD")
    again = prepare_workspace(toy["git_dir"], toy["manifest"], task, "guidance:raw", toy["tmp"] / "r2")
    assert again["start_sha"] == meta["start_sha"]


def test_arms_none_and_bridge(toy):
    task = _tasks(toy)["fix_add"]
    profile = load_profile(fake_harness(toy["tmp"])[1])
    none = prepare_workspace(toy["git_dir"], toy["manifest"], task, "guidance:none", toy["tmp"] / "n")
    assert none["arm_files"] == ["AGENTS.md", "sub/AGENTS.md"]
    assert not (toy["tmp"] / "n/workspace/sub/AGENTS.md").exists()
    bridge = prepare_workspace(toy["git_dir"], toy["manifest"], task, "guidance:bridge", toy["tmp"] / "b", profile)
    assert bridge["arm_files"] == ["CLAUDE.md", "sub/CLAUDE.md"]
    assert (toy["tmp"] / "b/workspace/sub/CLAUDE.md").read_text() == "@AGENTS.md\n"
    with pytest.raises(PrepError, match="bridge"):
        prepare_workspace(toy["git_dir"], toy["manifest"], task, "guidance:bridge", toy["tmp"] / "x")
    with pytest.raises(PrepError, match="unknown arm"):
        prepare_workspace(toy["git_dir"], toy["manifest"], task, "main-raw", toy["tmp"] / "x")


def test_a_patch_that_does_not_apply_is_a_prep_error(tmp_path):
    root, base = make_repo(tmp_path, {"fix_bad": {"family": "fix", "grader": "calc", "setup_patch": "setup.patch",
                                                  "prompt": "x"}})
    (root / ".agent-eval/tasks/fix_bad/setup.patch").write_text("--- a/nope.py\n+++ b/nope.py\n@@ -1 +1 @@\n-a\n+b\n")
    git(root, "commit", "-qam", "bad patch")
    source = RepoSource.parse(f"{root}@HEAD")
    tree = open_source(source)
    manifest = load_manifest(tree, ".agent-eval/repo-eval.yaml")
    task = {t.id: t for t in load_tasks(export_eval_dir(tree, manifest, tmp_path / "c"), manifest)}["fix_bad"]
    with pytest.raises(PrepError, match="does not apply"):
        prepare_workspace(source_git_dir(source, tree), manifest, task, "guidance:raw", tmp_path / "rd")


def test_build_context_and_eval_export_follow_contract_1_1(toy):
    ctx = export_build_context(toy["tree"], toy["manifest"], toy["tmp"] / "ctx")
    assert (ctx / ".agent-eval/Dockerfile").is_file() and (ctx / "src/calc.py").is_file()
    assert not (ctx / ".agent-eval/tasks").exists() and not (ctx / ".git").exists()
    assert (toy["content"] / "grade.py").is_file() and (toy["content"] / "tasks/fix_add/task.json").is_file()


def test_docker_argv_has_init_user_fixed_mounts_and_no_secret_values(tmp_path):
    spec = ContainerSpec(image="img", argv=["python3", "x"], mounts=[Mount(tmp_path, "/eval/run"),
                         Mount(tmp_path, CONTENT_DIR, read_only=True)],
                         env={"HOME": "/eval/run/home"}, pass_env=("AWS_BEARER_TOKEN_BEDROCK",), network="none",
                         name="dokimi-x", user="1000:1000")
    argv = DockerRunner().argv(spec)
    assert argv[:4] == ["docker", "run", "--rm", "--init"]
    # no sudo/setuid route to root-owned files in the bind mounts, and a ceiling on processes
    assert argv[4:10] == ["--security-opt", "no-new-privileges", "--cap-drop", "ALL", "--pids-limit", "32768"]
    assert ["--network", "none"] == argv[10:12] and "--user" in argv and "1000:1000" in argv
    assert f"type=bind,src={tmp_path},dst={CONTENT_DIR},readonly" in argv
    assert "AWS_BEARER_TOKEN_BEDROCK" in argv and "HOME=/eval/run/home" in argv
    assert argv[-3:] == ["img", "python3", "x"]
    with pytest.raises(ContainerError):
        DockerRunner().argv(ContainerSpec(image="i", argv=[], network="wide-open"))


@pytest.mark.parametrize("cmd", [["python3", "/eval/content/run.py", "grade"], ["python3", "/eval/content/run.py", "oracle"],
                                 ["claude", "-p", "x"], ["sh", "-c", "id"], ["git", "--version"]])
def test_docker_argv_clears_the_image_entrypoint_for_every_command(cmd):
    argv = DockerRunner().argv(ContainerSpec(image="img", argv=cmd))
    i = argv.index("--entrypoint")
    assert argv[i + 1] == "" and argv[i + 2] == "img"
    assert argv[argv.index("img"):] == ["img", *cmd]


def test_network_none_public_and_egress_refusal():
    assert docker_network("none") == "none" and docker_network("public") == "bridge"
    with pytest.raises(EgressNotImplemented, match="egress proxy not implemented"):
        docker_network("none", ("pypi.org",))


def test_render_entry_splits_then_substitutes_once():
    task = type("T", (), {"id": "t1"})()
    argv = render_entry("python3 {eval_dir}/run.py grade --contract {run_dir} --x '{task} a'", task)
    assert argv == ["python3", "/eval/content/run.py", "grade", "--contract", "/eval/run", "--x", "t1 a"]


def test_gate_entries():
    task = type("T", (), {"raw": {"gate": ["strict", {"metric": "recall", "min": 0.5}]}})()
    assert gate_ok(task, {"pass": True, "metrics": {"strict": True, "recall": 0.5}})
    assert not gate_ok(task, {"pass": True, "metrics": {"strict": True, "recall": True}})
    assert not gate_ok(task, {"pass": True, "metrics": {"strict": None, "recall": 1}})
    default = type("T", (), {"raw": {}})()
    assert gate_ok(default, {"pass": True, "metrics": {}}) and not gate_ok(default, {"pass": None, "metrics": {}})


def test_pass_null_is_score_only_only_for_a_task_with_score(tmp_path):
    (tmp_path / "grade.json").write_text(json.dumps({"schema": "repo_grade.v1", "pass": None, "metrics": {"recall": 1},
                                                     "errors": []}))
    done = ContainerResult(returncode=0, timed_out=False, wall_s=1.0)
    gated = outcome(Task("fix", {"gate": ["strict"]}, tmp_path), tmp_path, done)
    assert gated["status"] == "grader_failure" and gated["success"] is False and not gated["score_only"]
    scored = outcome(Task("rev", {"score": {"metric": "recall", "max": 1}}, tmp_path), tmp_path, done)
    assert scored["status"] == "graded" and scored["score_only"] and scored["score"] == 1.0


@pytest.mark.parametrize("value", ["0", "-1", "nan", "inf"])
def test_run_refuses_a_budget_that_caps_nothing(value, capsys):
    for flag in ("--budget", "--max-batch-usd"):
        with pytest.raises(SystemExit):
            build_parser().parse_args(["repo", "run", "x", "--arm", "guidance:raw", "--model", "m", "--batch", "b",
                                       flag, value])
        assert "positive number of USD" in capsys.readouterr().err


def test_timeout_kills_the_whole_process_group(tmp_path):
    marker = tmp_path / "child.pid"
    script = f"import os,subprocess,time; p=subprocess.Popen(['sleep','60']); open('{marker}','w').write(str(p.pid)); time.sleep(60)"
    res = run_process(["python3", "-c", script], env=dict(os.environ), stdout=tmp_path / "o", stderr=tmp_path / "e",
                      timeout_s=2)
    assert res.timed_out and res.returncode is None
    pid = int(marker.read_text())
    alive = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()
    assert alive in ("", "Z")  # gone, or a zombie waiting for init


def test_hook_denial_in_claude_trace_marks_the_command_denied():
    lines = [{"type": "assistant", "message": {"content": [
                 {"type": "tool_use", "id": "a", "name": "Bash", "input": {"command": "make &"}}]}},
             {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "a", "is_error": True,
                 "content": "PreToolUse:Bash hook error: Background commands are disabled"}]}},
             {"type": "result", "subtype": "success", "result": "ok", "is_error": False}]
    tr = convert("claude-stream-json", "\n".join(json.dumps(x) for x in lines))
    assert tr.events[0]["denied"] is True and tr.events[0]["rc"] is None and tr.result_subtype == "success"


def test_no_background_hook():
    hook = Path(__file__).resolve().parents[1] / "dokimi/repos/support/no_background.py"

    def run(tool_input):
        return subprocess.run(["python3", str(hook)], input=json.dumps({"tool_input": tool_input}),
                              capture_output=True, text=True).returncode

    assert run({"command": "bazel build //x &"}) == 2
    assert run({"command": "nohup make"}) == 2 and run({"command": "make", "run_in_background": True}) == 2
    assert run({"command": "make && echo '&' 2>&1"}) == 0


def test_no_background_denies_a_quoted_shell_ampersand():
    hook = Path(__file__).resolve().parents[1] / "dokimi/repos/support/no_background.py"

    def run(command):
        return subprocess.run(["python3", str(hook)], input=json.dumps({"tool_input": {"command": command}}),
                              capture_output=True, text=True).returncode

    assert run('bash -c "sleep 1 &"') == 2 and run("sh -c 'make &'") == 2
    assert run('/bin/bash -lc "make all & wait"') == 2 and run("env A=1 sh -c 'nohup make'") == 2
    assert run('echo "a & b"') == 0 and run("bash -c 'make && make test'") == 0


def test_workspace_has_no_reflog_or_unreachable_objects(toy):
    for task_id, arm in (("fix_add", "guidance:none"), ("review_add", "guidance:raw")):
        rd = toy["tmp"] / f"ws-{task_id}"
        prepare_workspace(toy["git_dir"], toy["manifest"], _tasks(toy)[task_id], arm, rd)
        ws = rd / "workspace"
        history = set(git(ws, "rev-list", "HEAD").split())
        assert set(git(ws, "reflog", "--format=%H").split()) <= history  # nothing prep replaced
        assert git(ws, "fsck", "--unreachable", "--no-reflogs") == ""
        objects = subprocess.run(["git", "-C", str(ws), "cat-file", "--batch-all-objects", "--batch"],
                                 capture_output=True, check=True).stdout
        assert b"Upstream Person" not in objects and b"upstream@example.com" not in objects
        if task_id == "fix_add":  # neither the removed guidance nor the unpatched code is recoverable
            assert b"# guidance" not in objects and b"return a + b" not in objects
            assert subprocess.run(["git", "-C", str(ws), "rev-parse", "-q", "--verify", "HEAD@{1}"],
                                  capture_output=True).returncode != 0


def test_docker_cli_env_keeps_host_HOME_and_USER(tmp_path, monkeypatch):
    from dokimi import process

    fake = process.FakeRunner()
    monkeypatch.setenv("HOME", "/home/host-user")
    monkeypatch.setenv("USER", "host-user")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "s3cret")
    spec = ContainerSpec(image="img", argv=["true"], env={"HOME": "/eval/run/home", "USER": "eval", "X": "1"},
                         pass_env=("ANTHROPIC_API_KEY",))
    DockerRunner(runner=fake).run(spec, stdout=tmp_path / "o", stderr=tmp_path / "e", timeout_s=5)
    (call,) = fake.calls
    seen = {"argv": list(call.argv), "env": call.env}
    assert call.stdout_to == tmp_path / "o" and call.stderr_to == tmp_path / "e" and call.timeout == 5
    assert seen["env"]["HOME"] == "/home/host-user" and seen["env"]["USER"] == "host-user"
    assert "X" not in seen["env"] or seen["env"]["X"] == os.environ.get("X")
    argv = seen["argv"]
    assert "HOME=/eval/run/home" in argv and "USER=eval" in argv and "X=1" in argv
    assert "ANTHROPIC_API_KEY" in argv and "s3cret" not in " ".join(argv)


@pytest.mark.parametrize("metrics", [{}, {"recall": True}, {"recall": None}], ids=["missing", "bool", "null"])
def test_score_only_grade_without_a_numeric_score_metric_is_grader_failure(tmp_path, metrics):
    (tmp_path / "grade.json").write_text(json.dumps({"schema": "repo_grade.v1", "pass": None, "metrics": metrics,
                                                     "errors": []}))
    done = ContainerResult(returncode=0, timed_out=False, wall_s=1.0)
    got = outcome(Task("rev", {"score": {"metric": "recall", "max": 1}}, tmp_path), tmp_path, done)
    assert got["status"] == "grader_failure" and got["score"] is None
    assert "no numeric metrics.recall" in got["reasons"][-1]


# ------------------------------------------------------------------ tasks check, through the CLI


def _cli(argv, monkeypatch, runner):
    monkeypatch.setattr("dokimi.repos.container.DockerRunner", lambda *a, **k: runner)
    args = build_parser().parse_args(argv)
    args.func(args)


def _check_argv(root, out, *extra):
    return ["repo", "tasks", "check", f"{root}@HEAD", "--manifest", ".agent-eval/repo-eval.yaml",
            "--out", str(out), "--cache-root", str(out / "caches"), *extra]


def test_tasks_check_ok_builds_image_without_tasks(toy, monkeypatch, capsys):
    runner = FakeRunner()
    _cli(_check_argv(toy["root"], toy["tmp"] / "chk"), monkeypatch, runner)
    out = json.loads((toy["tmp"] / "chk/check/check.json").read_text())
    assert {t["task"]: t["verdict"] for t in out["tasks"]} == {"fix_add": "ok", "review_add": "ok"}
    review = next(t for t in out["tasks"] if t["task"] == "review_add")
    assert review["oracle"]["score_only"] and review["oracle"]["score"] == 1.0 and review["null"]["score"] == 0.0
    assert len(runner.builds) == 1 and not any(p.startswith(".agent-eval/tasks") for p in runner.builds[0][1])
    grade_specs = [s for s in runner.specs if "grade" in s.argv]
    assert grade_specs and all(s.network == "none" for s in grade_specs)
    assert all(any(m.container == CONTENT_DIR and m.read_only for m in s.mounts) for s in grade_specs)
    oracle = [s for s in runner.specs if "oracle" in s.argv]
    assert all(any(m.container == SOURCE_DIR and m.read_only for m in s.mounts) for s in oracle)
    assert not any(m.container == SOURCE_DIR for s in grade_specs for m in s.mounts)  # not named by `grade`
    assert all(s.env["HOME"] == "/eval/run/grade-home" and s.env["USER"] == "eval" for s in grade_specs + oracle)
    assert "2/2 ok" in capsys.readouterr().out


def _broken_variant(tmp_path, grader_patch: str, task_extra=None):
    root, _ = make_repo(tmp_path, task_extra)
    grader = root / ".agent-eval/grade.py"
    grader.write_text(grader.read_text().replace(*grader_patch.split("|||")))
    git(root, "commit", "-qam", "variant")
    return root


@pytest.mark.parametrize("patch, problem", [
    ('write_text("def add(a, b):\\n    return a + b\\n")|||write_text("def add(a, b):\\n    return a * b\\n")',
     "oracle: the reference solution does not pass the gate"),
    ('ok = mod.add(2, 3) == 5|||ok = True', "null: the untouched workspace passes the gate"),
    ('meta = json.loads|||raise SystemExit(3)\nmeta = json.loads', "null: grader failure (grade exited 3"),
    ('recall = 1.0 if|||recall = 0.5 if', "oracle: recall = 0.5, below score.max 1.0"),
])
def test_tasks_check_finds_broken_graders(tmp_path, monkeypatch, capsys, patch, problem):
    root = _broken_variant(tmp_path, patch)
    with pytest.raises(SystemExit) as exc:
        _cli(_check_argv(root, tmp_path / "chk"), monkeypatch, FakeRunner())
    assert exc.value.code == 1
    out = json.loads((tmp_path / "chk/check/check.json").read_text())
    problems = [p for t in out["tasks"] for p in t["problems"]]
    assert any(problem in p for p in problems), problems


def test_tasks_check_refuses_egress_and_flags_a_leaky_image(tmp_path, monkeypatch, capsys):
    root, _ = make_repo(tmp_path, {"fix_pip": {"family": "fix", "grader": "calc", "egress": ["pypi.org"],
                                               "prompt": "x"}})
    with pytest.raises(SystemExit):
        _cli(_check_argv(root, tmp_path / "a", "--task", "fix_pip"), monkeypatch, FakeRunner())
    res = json.loads((tmp_path / "a/check/check.json").read_text())["tasks"][0]
    assert res["verdict"] == "unsupported" and "egress proxy not implemented" in res["problems"][0]
    runner = FakeRunner()
    runner.leak = True
    with pytest.raises(SystemExit):
        _cli(_check_argv(root, tmp_path / "b", "--task", "fix_add"), monkeypatch, runner)
    res = json.loads((tmp_path / "b/check/check.json").read_text())["tasks"][0]
    assert res["verdict"] == "broken" and "isolation: named like a task" in res["problems"][0]


# ------------------------------------------------------------------ review of #82, round 4


def test_workspace_drops_replaced_objects_that_were_packed(toy, monkeypatch):
    import dokimi.repos.workspace as workspace

    forget = workspace._forget

    def packed_first(ws, env):  # as a detached `gc --auto` would have, on a large tree
        workspace._git(ws, env, "gc", "-q")
        assert list((ws / ".git/objects/pack").glob("*.pack"))
        forget(ws, env)

    monkeypatch.setattr(workspace, "_forget", packed_first)
    rd = toy["tmp"] / "ws-packed"
    prepare_workspace(toy["git_dir"], toy["manifest"], _tasks(toy)["fix_add"], "guidance:none", rd)
    ws = rd / "workspace"
    assert git(ws, "fsck", "--unreachable", "--no-reflogs") == ""
    objects = subprocess.run(["git", "-C", str(ws), "cat-file", "--batch-all-objects", "--batch"],
                             capture_output=True, check=True).stdout
    assert b"# guidance" not in objects and b"return a + b" not in objects


def test_trees_are_checked_out_without_host_config_or_export_attributes(tmp_path, monkeypatch):
    from dokimi.repos import parse_manifest
    from dokimi.repos.source import GitTree
    from repo_eval_fixture import MANIFEST

    root = tmp_path / "repo"
    files = {"a.txt": "x\n", "keep/t.py": "pass\n", "subst.txt": "$Format:%H$\n", ".agent-eval/g.py": "y\n",
             ".gitattributes": "keep/** export-ignore\n.agent-eval/** export-ignore\nsubst.txt export-subst\n"}
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)
    git(root, "init", "-q")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "x")
    sha = git(root, "rev-parse", "HEAD")
    home = tmp_path / "home"
    home.mkdir()
    (home / ".gitconfig").write_text("[core]\n\tautocrlf = true\n")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("GIT_CONFIG_GLOBAL", raising=False)
    manifest = parse_manifest(MANIFEST)
    task = Task("t", {"base": sha}, tmp_path)
    prepare_workspace(root / ".git", manifest, task, "guidance:raw", tmp_path / "rd")
    tree = GitTree(root / ".git", sha)
    content = export_eval_dir(tree, manifest, tmp_path / "content")
    ctx = export_build_context(tree, manifest, tmp_path / "ctx")
    for out in (tmp_path / "rd/workspace", ctx):
        assert (out / "a.txt").read_bytes() == b"x\n" and (out / "keep/t.py").is_file()
        assert (out / "subst.txt").read_text() == "$Format:%H$\n"
    assert not (tmp_path / "rd/workspace/.agent-eval").exists()  # eval_dir is still removed (step 2)
    assert (content / "g.py").read_bytes() == b"y\n"


def test_a_docker_kill_that_times_out_still_ends_the_run(tmp_path):
    """The CLI gets SIGTERM (it stops the container) before SIGKILL; then the container is
    killed by name, and a `docker kill` that hangs too still ends the run."""
    from dokimi import process

    fake = process.FakeRunner(lambda call: process.reply("partial", timed_out=True))
    spec = ContainerSpec(image="img", argv=["true"], name="dokimi-x")
    res = DockerRunner("docker", runner=fake).run(spec, stdout=tmp_path / "o", stderr=tmp_path / "e",
                                                  timeout_s=1)
    assert res.timed_out and res.returncode is None
    run, kill = fake.calls
    assert run.argv[:2] == ("docker", "run") and run.term_grace > 0 and run.timeout == 1
    assert kill.argv == ("docker", "kill", "dokimi-x") and kill.timeout == 60
    assert (tmp_path / "o").read_text() == "partial"


def test_a_docker_cli_that_cannot_start_is_a_container_error(tmp_path):
    """No docker binary: the build is a ContainerError, and a timed-out run whose `docker kill`
    cannot start either still returns as timed out."""
    from dokimi import process

    def missing(call):
        raise FileNotFoundError(2, "No such file or directory", call.argv[0])

    def missing_kill(call):
        return missing(call) if call.argv[1] == "kill" else process.reply(timed_out=True)

    with pytest.raises(ContainerError, match="cannot run 'nodocker'"):
        DockerRunner("nodocker", runner=process.FakeRunner(missing)).build(tmp_path, tmp_path / "D", "t")
    res = DockerRunner(runner=process.FakeRunner(missing_kill)).run(
        ContainerSpec(image="img", argv=["true"], name="dokimi-y"), stdout=tmp_path / "o", stderr=tmp_path / "e",
        timeout_s=1)
    assert res.timed_out and res.returncode is None


def test_docker_build_and_inspect_are_bounded(tmp_path):
    """A wedged daemon fails the build or the image lookup (a ContainerError) instead of hanging
    the batch; an image that is not there is still just absent."""
    import dokimi.repos.container as container
    from dokimi import process

    fake = process.FakeRunner(lambda call: process.reply(stderr="step 3/9", timed_out=True))
    runner = DockerRunner(runner=fake)
    with pytest.raises(ContainerError, match="timed out after"):
        runner.build(tmp_path, tmp_path / "Dockerfile", "img:t")
    with pytest.raises(ContainerError, match="no answer in 60s"):
        runner.image_exists("img:t")
    build, inspect = fake.calls
    assert build.timeout == container.BUILD_TIMEOUT_S and inspect.timeout == container.DOCKER_CALL_TIMEOUT_S
    missing = DockerRunner(runner=process.FakeRunner(lambda call: process.reply(returncode=1)))
    assert missing.image_exists("img:t") is False and DockerRunner(runner=process.FakeRunner()).image_exists("x")


def test_workspace_git_never_prompts_and_a_timeout_is_a_prep_error(monkeypatch, tmp_path):
    """A clone of a private remote must not wait on a credential prompt until the 30-min timeout;
    the clone keeps the caller's env (credential helpers), a snapshot step gets exactly its own."""
    import dokimi.repos.workspace as workspace
    from dokimi import process

    fake = process.FakeRunner(lambda call: process.reply(timed_out=True) if "fetch" in call.argv else None)
    monkeypatch.setattr(workspace.process, "run", fake)
    monkeypatch.setenv("DOKIMI_TEST_ONLY_HELPER", "x")
    bare = str(tmp_path / "x")  # never created: the fake runner spawns nothing
    workspace._run(["git", "clone", "--bare", "--", "https://example.invalid/r.git", bare])
    workspace._run(["git", "-C", bare, "status"], env={"PATH": "/usr/bin"})
    with pytest.raises(PrepError, match="timed out"):
        workspace._run(["git", "-C", bare, "fetch", "origin"])
    clone, status, _ = fake.calls
    assert clone.env["GIT_TERMINAL_PROMPT"] == "0" and clone.env["DOKIMI_TEST_ONLY_HELPER"] == "x"
    assert status.env == {"PATH": "/usr/bin", "GIT_TERMINAL_PROMPT": "0"}
    assert {c.timeout for c in fake.calls} == {workspace._GIT_TIMEOUT_S}
    assert {c.capture_limit for c in fake.calls} == {None}  # ls-files, rev-parse: data, never cut


@pytest.mark.parametrize("which", range(4))
def test_a_clone_that_needed_a_prompt_says_which_and_what_to_do(monkeypatch, which):
    """ssh's "Host key verification failed" sits above three lines of boilerplate; it, not the
    tail, is what a PrepError must show, with a hint (clone and fetch never prompt)."""
    import dokimi.repos.workspace as workspace
    from dokimi import process
    from test_process import GIT_PROMPT_FAILURES

    stderr, line, hint = GIT_PROMPT_FAILURES[which]
    monkeypatch.setattr(workspace.process, "run", process.FakeRunner(lambda call: process.reply(
        stderr=stderr, returncode=128)))
    with pytest.raises(PrepError) as err:
        workspace._run(["git", "clone", "--bare", "--", "git@github.com:o/r.git", "/x"])
    assert str(err.value).startswith("git clone --bare -- git@github.com:o/r.git exited 128: " + line)
    assert "hint: " in str(err.value) and hint in str(err.value)


def test_a_patch_failing_on_two_files_names_both(tmp_path):
    import dokimi.repos.workspace as workspace

    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "a.txt").write_text("one\n")
    (ws / "b.txt").write_text("one\n")
    patch = tmp_path / "bad.patch"
    patch.write_text("".join(f"--- a/{n}\n+++ b/{n}\n@@ -1 +1 @@\n-zero\n+two\n" for n in ("a.txt", "b.txt")))
    env = {"PATH": os.environ["PATH"], "HOME": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
    workspace._run(["git", "-C", str(ws), "init", "-q"], env=env)
    with pytest.raises(PrepError) as err:
        workspace._run(["git", "-C", str(ws), "apply", str(patch)], env=env)
    assert "a.txt: patch does not apply" in str(err.value) and "b.txt: patch does not apply" in str(err.value)
    assert " | " in str(err.value)


def test_a_timed_out_cat_file_is_an_error_not_an_absent_object(tmp_path, monkeypatch):
    from dokimi import process
    from dokimi.repos import workspace

    hung = process.Completed(("git",), process.TIMEOUT_RC, b"", b"", True, 1.0)
    monkeypatch.setattr(process, "git", lambda *a, **k: hung)
    with pytest.raises(PrepError, match="timed out"):
        workspace._has_object(tmp_path, "HEAD:x")
    with pytest.raises(PrepError, match="timed out"):
        workspace._has_commit(tmp_path, "abc")
