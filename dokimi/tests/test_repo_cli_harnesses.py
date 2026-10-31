# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Codex and Copilot CLI profiles and native review answers (contract §4.1, §6); traces: test_repo_cli_traces.py. No LLM."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
import yaml

from dokimi.cli import build_parser
from dokimi.repos.answers import codex_review_findings, convert_answer
from dokimi.repos.harness_profile import ProfileError, builtin_profiles, load_profile, parse_profile
from dokimi.repos.reach import ReachError, probe_files
from repo_eval_fixture import FakeRunner, make_repo, write

# The fake harnesses are `#!/usr/bin/env python3` scripts run through chmod 0o755, and some tests symlink them:
# neither works on Windows (symlinks need a privilege there).
_POSIX_FAKE_EXE = pytest.mark.skipif(sys.platform == "win32", reason="a POSIX script and a symlink stand for the harness")

# --- profiles ----------------------------------------------------------------


def test_builtin_cli_profiles_load_and_build_argv_without_a_shell():
    assert {"codex", "codex-review", "copilot", "copilot-review"} <= set(builtin_profiles())
    prompt = '-say "hi"; {model}'
    codex = load_profile("codex").argv(prompt=prompt, model="gpt-x", tools="all", bin_path="/opt/codex")
    assert codex[:2] == ["/opt/codex", "exec"] and codex[-2:] == ["--", prompt]  # after --, never a flag
    assert "--json" in codex and "--dangerously-bypass-approvals-and-sandbox" in codex
    copilot = load_profile("copilot")
    # one element: a separate -p value starting with "-" makes Copilot 1.0.100 exit 1 before any model call
    assert copilot.argv(prompt=prompt, model="m", tools="all")[1] == f"--prompt={prompt}"
    assert copilot.argv(prompt=prompt, model="m", tools="read")[-4:] == ["--available-tools", "view", "grep", "glob"]
    # an empty --available-tools list would leave every tool on
    assert copilot.argv(prompt=prompt, model="m", tools="none")[-2:] == ["--available-tools", "__none__"]
    review = load_profile("copilot-review").argv(prompt="Review HEAD.", model="m")
    assert review[review.index("-p") + 1] == "/review Review HEAD."
    assert load_profile("codex").guidance_names == ("AGENTS.md", "AGENTS.override.md")
    assert "copilot-instructions.md" in copilot.guidance_names


@pytest.mark.parametrize("tools", ["none", "read", "all"])
def test_codex_review_runs_its_own_prompt(tools):
    profile = load_profile("codex-review")
    assert profile.native_prompt and profile.answer_format == "codex-review" and profile.trace == "codex-json"
    argv = profile.argv(prompt="Review HEAD as a maintainer", model="gpt-x", tools=tools)
    review = argv.index("review")
    assert argv[1] == "exec" and argv[review:review + 3] == ["review", "--commit", "HEAD"]
    # `codex exec review` rejects --sandbox (0.160, 0.163): the tool mode is an `exec` option
    # the flags per tool mode are pinned as literals: a wrong codex-review.yaml entry must fail here
    flags = {"none": ["--sandbox", "read-only"], "read": ["--sandbox", "read-only"],
             "all": ["--dangerously-bypass-approvals-and-sandbox"]}
    assert argv[2:review] == flags[tools] and "--sandbox" not in argv[review:]
    assert not any("Review HEAD as" in a for a in argv)  # `--commit` refuses a custom prompt
    assert "--json" in argv and "--skip-git-repo-check" in argv and argv[argv.index("--model") + 1] == "gpt-x"


def test_profile_env_passes_byok_names_only():
    env = load_profile("copilot").env(Path("/cfg")).resolve({
        "COPILOT_PROVIDER_BASE_URL": "http://127.0.0.1:4000/v1", "COPILOT_PROVIDER_API_KEY": "k",
        "AWS_BEARER_TOKEN_BEDROCK": "x", "PATH": "/bin"})
    assert env["COPILOT_HOME"] == "/cfg" and env["COPILOT_AUTO_UPDATE"] == "false"
    assert env["COPILOT_PROVIDER_BASE_URL"] == "http://127.0.0.1:4000/v1"
    assert "AWS_BEARER_TOKEN_BEDROCK" not in env


@pytest.mark.parametrize("name", ["copilot", "copilot-review"])
def test_copilot_profiles_forward_only_the_token_copilot_hides_from_its_shell(name):
    env = load_profile(name).env(Path("/cfg")).resolve(
        {"COPILOT_GITHUB_TOKEN": "c", "GH_TOKEN": "g", "GITHUB_TOKEN": "t", "PATH": "/bin"})
    assert env["COPILOT_GITHUB_TOKEN"] == "c" and "GH_TOKEN" not in env and "GITHUB_TOKEN" not in env
    assert load_profile("codex").env(Path("/c")).resolve({"OPENAI_API_KEY": "k"})["CODEX_HOME"] == "/c"


@pytest.mark.parametrize("mutate, needle", [
    (lambda r: r.update(native_prompt=False), "never passes {prompt}"),
    (lambda r: r["invoke"].insert(1, "{prompt}"), "cannot pass"),
    (lambda r: r.update(prompt_suffix="x"), "prompt_suffix"),
    (lambda r: r.update(answer_format="xml"), "answer_format"),
    (lambda r: r.update(trace="codex"), "trace"),
])
def test_bad_native_profile_is_refused(mutate, needle):
    raw = yaml.safe_load(Path(load_profile("codex-review").path).read_text())
    mutate(raw)
    with pytest.raises(ProfileError) as exc:
        parse_profile(raw)
    assert needle in str(exc.value)


@pytest.mark.parametrize("name", ["../x", "a/b", "/etc/passwd", ".hidden", "..\\x", "a\\b", "C:x"])
def test_bin_companions_are_plain_file_names(name):
    # harness_mounts joins each name to the --bin directory and mounts the result into the agent
    raw = yaml.safe_load(Path(load_profile("codex").path).read_text())
    raw["bin_companions"] = [name]
    with pytest.raises(ProfileError, match="bin_companions"):
        parse_profile(raw)


@pytest.mark.parametrize("name", [".", "..", "", "a/b", "/etc/x", "a\\b"])
def test_config_file_names_are_plain_file_names(name):
    from repo_eval_fixture import PROFILE

    with pytest.raises(ProfileError, match="config_files"):
        parse_profile({**PROFILE, "config_files": {name: "x"}})


def test_reach_refuses_a_native_prompt_profile(tmp_path):
    root, _ = make_repo(tmp_path)
    from dokimi.repos import RepoSource, load_manifest, open_source

    tree = open_source(RepoSource.parse(f"{root}@HEAD"))
    manifest = load_manifest(tree, ".agent-eval/repo-eval.yaml")
    with pytest.raises(ReachError, match="native_prompt"):
        probe_files(tree, manifest, load_profile("codex-review"))
    assert "AGENTS.md" in probe_files(tree, manifest, load_profile("codex"))


# --- native review answers ---------------------------------------------------

CODEX_REVIEW = """The loop reads past the end.

Full review comments:

- [P1] Use a C++17-compatible span — /ws/cpp/x/scan.hpp:6-6
  When this header is built as C++17, `std::span` is unavailable.
  Use a pointer and a length.

- [P2] Stop before reading past the input — /ws/src/f.cpp:64-66
  This loop also reads `a[n]`.
"""


def test_codex_review_findings():
    found = codex_review_findings(CODEX_REVIEW, "/ws")
    assert found == [
        {"file": "cpp/x/scan.hpp", "line": 6, "end_line": 6, "severity": "major", "priority": "P1",
         "rule": "Use a C++17-compatible span",
         "message": "When this header is built as C++17, `std::span` is unavailable. Use a pointer and a length."},
        {"file": "src/f.cpp", "line": 64, "end_line": 66, "severity": "minor", "priority": "P2",
         "rule": "Stop before reading past the input", "message": "This loop also reads `a[n]`."},
    ]
    assert codex_review_findings("Review comment:\n\n- [P0] Crash — a.c:3\n  boom") == [
        {"file": "a.c", "line": 3, "end_line": 3, "severity": "blocker", "priority": "P0", "rule": "Crash",
         "message": "boom"}]
    assert codex_review_findings("No issues found.") == []


@pytest.mark.parametrize("name", ["codex", "copilot", "claude-code"])
def test_execution_id_pinned_by_trace(name):
    # another converter gives another answer and other read events: a stored cell must not be reused
    import dataclasses

    profile = load_profile(name)
    assert dataclasses.replace(profile, trace="text").execution_id("all") != profile.execution_id("all")


def test_builtin_codex_profiles_do_not_forward_openai_api_key():
    # codex exec never reads it, and in `all` mode the agent's shell would see it
    for name in ("codex", "codex-review"):
        assert "OPENAI_API_KEY" not in load_profile(name).env_allow, name


@pytest.mark.parametrize("sep", ["\u2028", "\u2029", "\x0b", "\x0c", "\x1c", "\x85"])
def test_codex_review_body_with_a_unicode_line_break_is_not_cut_short(sep):
    # str.splitlines also breaks at these; the markers are found by \n only, so the body must split on \n too
    text = f"- [P1] T \u2014 a.c:1\n  first{sep}second part\n  third\n"
    assert codex_review_findings(text)[0]["message"] == f"first{sep}second part third"


def test_codex_review_findings_titles_with_dashes_and_trailing_prose():
    text = ("- [P1] Leaks \u2014 secrets \u2014 /ws/a.py:3\n  body one\n\nOverall: patch is incorrect.\n")
    assert codex_review_findings(text, "/ws") == [
        {"file": "a.py", "line": 3, "end_line": 3, "severity": "major", "priority": "P1",
         "rule": "Leaks \u2014 secrets", "message": "body one"}]
    last = "- [P2] T \u2014 a.c:1\n  body\nOverall: bad."  # a non-indented line ends the body too
    assert codex_review_findings(last)[0]["message"] == "body"
    paragraphs = "- [P1] T \u2014 a.c:1\n  first\n\n  second\n\nOverall: bad."  # a body spans blank lines
    assert codex_review_findings(paragraphs)[0]["message"] == "first second"


def test_convert_answer_leaves_a_failed_run_as_is():
    assert convert_answer("codex-review", "I will inspect the commit first.", failed=True) == (
        "I will inspect the commit first.")
    assert convert_answer("codex-review", "No issues.").endswith("```json\n[]\n```\n")


def test_convert_answer_leaves_an_empty_answer_empty():
    # a failed or timed-out run must not read as a clean review (an empty findings block)
    assert convert_answer("codex-review", "") == ""
    assert convert_answer("codex-review", "  \n") == "  \n"


def test_convert_answer_leaves_an_empty_final_answer_as_is_even_after_a_narrated_message():
    # an earlier message with text does not make an empty final answer a review: it stays a missing one
    assert convert_answer("codex-review", "", "/ws", messages=["I will inspect the commit first.", ""]) == ""


def test_convert_answer_maps_every_severity_and_separates_the_block(caplog):
    text = "\n".join(f"- [P{n}] t{n} \u2014 f{n}.c:{n}\n  b" for n in (0, 1, 2, 3, 9))
    out = convert_answer("codex-review", text)
    head, block = out.split("\n\n```json\n")
    assert head == text and block.endswith("\n```\n")
    assert [f["severity"] for f in json.loads(block.split("\n```")[0])] == ["blocker", "major", "minor", "minor", None]
    assert "unknown priority P9" in caplog.text and caplog.text.count("WARNING") == 1
    drift = "- [p1] lowercase \u2014 a.c:1\n  b\n- [P1] hyphen - a.c:2\n"
    with caplog.at_level("WARNING"):
        assert convert_answer("codex-review", drift) == drift  # no block: not a clean review
    assert "2 finding marker(s)" in caplog.text and "0 parsed" in caplog.text


def test_convert_answer_reads_an_indented_finding_header():
    # an indented header is a finding like any other: the block carries it, so it is not a clean review
    indented = "Findings:\n  - [P1] Leak — /ws/a.c:2\n    body\n"
    out = convert_answer("codex-review", indented, "/ws")
    assert out.startswith(indented)
    assert json.loads(out.rsplit("```json\n", 1)[1].split("\n```", 1)[0]) == [
        {"file": "a.c", "line": 2, "end_line": 2, "severity": "major", "priority": "P1", "rule": "Leak",
         "message": "body"}]


def test_convert_answer_reads_a_path_bearing_bullet_in_a_body_as_a_finding():
    # a bullet with a path is a finding header in any shape: it is parsed, so it is never counted and dropped
    text = "- [P1] t — /workspace/a.py:3\n  - [P2] sub — /workspace/b.py:4\n  body\n"
    out = convert_answer("codex-review", text, "/workspace")
    assert "```json\n" in out
    assert [(f["file"], f["priority"]) for f in json.loads(out.rsplit("```json\n", 1)[1].split("\n```", 1)[0])] == [
        ("a.py", "P1"), ("b.py", "P2")]


def test_codex_review_findings_does_not_join_a_path_across_a_line_break():
    # a header with no :line must not run on into the next body line
    text = "- [P1] t — src/a.py\n  see the code:12\n"
    assert codex_review_findings(text, "/ws") == []
    assert convert_answer("codex-review", text, "/ws") == text  # one marker, no finding: no block


def test_convert_answer_does_not_count_a_bullet_inside_a_finding_body():
    # a [Pn] inside a body is prose: the one parsed finding keeps its block
    text = "- [P1] t — /workspace/a.py:3\n  see [P1] earlier\n  - [P2] sub bullet\n"
    out = convert_answer("codex-review", text, "/workspace")
    assert out.startswith(text)
    assert [f["priority"] for f in json.loads(out.rsplit("```json\n", 1)[1].split("\n```", 1)[0])] == ["P1"]


@pytest.mark.parametrize("line", ["  - [P2] see b.py:4 too\n", "  1. [P2] run at 10:30\n"],
                         ids=["path-cited-mid-line", "time-in-numbered-step"])
def test_convert_answer_keeps_a_body_bullet_that_cites_a_line(line):
    # a body bullet that cites file:line is prose, not a drifted header: the one parsed finding keeps its block
    text = "- [P1] A — /w/a.py:3\n  body\n" + line
    out = convert_answer("codex-review", text, "/w")
    assert out.startswith(text)
    assert [f["priority"] for f in _block(out)] == ["P1"]


def test_convert_answer_withholds_the_block_for_an_indented_drifted_header_after_a_finding():
    # B uses a hyphen, not the em dash: it is a header that did not parse, not body text
    text = "- [P1] A — /w/a.py:3\n  body\n  - [P2] B - /w/b.py:4\n    body\n"
    assert convert_answer("codex-review", text, "/w") == text


def test_convert_answer_withholds_the_block_for_a_drifted_header_with_no_path():
    # the em dash alone makes a header: "[P2] B — no location" is a drifted header, not body text
    text = "- [P1] A — /w/a.py:3\n  body\n  - [P2] B — no location given\n    body\n"
    assert convert_answer("codex-review", text, "/w") == text


@pytest.mark.parametrize("header", [
    "  - [P2] title — /w/b.py\n",  # no line number
    "  - [P2] title — `/w/b.py:4`\n",  # backticked path
    "  - [P2] title — /w/b.py:4.\n",  # trailing period
    "  - [P2] title — /w/b.py:4 (note)\n",  # trailing note
    "  - [P2] title —/w/b.py:4\n",  # no space before the dash
    "  - [P2] title /w/Makefile:4\n",  # no dash, an extensionless path
    "  - [P2] title -/w/Makefile:4\n",  # an ASCII dash with no space, an extensionless path
])
def test_convert_answer_withholds_the_block_for_a_drifted_header_of_any_shape(header):
    # a P2 header that did not parse must not fold into the P1 body: the block is withheld
    text = "- [P1] A — /w/a.py:3\n  body\n" + header
    assert convert_answer("codex-review", text, "/w") == text


@pytest.mark.parametrize("answer", [
    "  [P1] Incorrect validation — /ws/a.py:12",
    "- [P2] Old — /ws/b.py:3\n  body\n  [P1] New — /ws/c.py:4",
    "- [P2] Old — /ws/b.py:3\n  body\n  [P1] New without path\n",  # no path, no dash: still a header, not body
    "- [P2] Old — /ws/b.py:3\n  body\n  [P1] New without path",
])
def test_convert_answer_withholds_the_block_for_an_indented_bare_header(answer):
    # an indented [Pn] header with no bullet is a finding that did not parse: no block, not a clean review
    assert convert_answer("codex-review", answer, "/ws") == answer


def test_codex_review_findings_accepts_crlf_line_endings():
    text = "- [P1] t — /workspace/a.py:3\r\n  body\r\n"
    assert codex_review_findings(text, "/workspace") == [
        {"file": "a.py", "line": 3, "end_line": 3, "severity": "major", "priority": "P1", "rule": "t",
         "message": "body"}]
    assert json.loads(convert_answer("codex-review", text, "/workspace").rsplit("```json\n", 1)[1]
                      .split("\n```", 1)[0])[0]["message"] == "body"


def test_convert_answer_appends_a_last_json_block_and_keeps_the_text():
    out = convert_answer("codex-review", CODEX_REVIEW, "/ws")
    assert out.startswith(CODEX_REVIEW)
    block = out.rsplit("```json\n", 1)[1].split("\n```", 1)[0]
    assert [f["file"] for f in json.loads(block)] == ["cpp/x/scan.hpp", "src/f.cpp"]
    assert convert_answer(None, "as is") == "as is"
    with pytest.raises(ValueError, match="unknown answer format"):
        convert_answer("xml", "x")


FAKE_CODEX_REVIEW = r'''#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
if args == ["--version"]:
    print("codex-cli 0.160.0"); sys.exit(0)
review = args.index("review")
assert args[0] == "exec" and args[review:review + 3] == ["review", "--commit", "HEAD"], args
assert "--sandbox" not in args[review:], "error: unexpected argument '--sandbox' found"
text = ("One bug.\n\nReview comment:\n\n- [P1] add() subtracts — " + os.environ["FAKE_CONTAINER_CWD"]
        + "/src/calc.py:2-2\n  The change turns a + b into a - b.")
print(json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": text}}))
print(json.dumps({"type": "turn.completed", "usage": {"input_tokens": 0, "output_tokens": 0}}))
'''


# What codex 0.163 `exec review` prints when the turn fails: its own message, then turn.failed.
FAKE_CODEX_REVIEW_FAILED = r'''#!/usr/bin/env python3
import json, sys
if sys.argv[1:] == ["--version"]:
    print("codex-cli 0.163.0"); sys.exit(0)
text = "Review was interrupted. Please re-run /review and wait for it to complete."
print(json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": text}}))
print(json.dumps({"type": "turn.failed", "error": {"message": "unexpected status 401 Unauthorized"}}))
sys.exit(1)
'''


def _run_codex_review(tmp_path, monkeypatch, fake, tools="all"):
    root, _ = make_repo(tmp_path)
    exe = tmp_path / "bin" / "codex"
    write(exe.parent, exe.name, fake)
    exe.chmod(0o755)
    write(exe.parent, "codex-code-mode-host", "")  # the profile's bin_companions
    raw = yaml.safe_load(Path(load_profile("codex-review").path).read_text())
    raw.update(env_allow=["PATH"], egress=["api.example.com"])
    profile = tmp_path / "codex-review.yaml"
    profile.write_text(yaml.safe_dump(raw), encoding="utf-8")
    runner = FakeRunner()
    monkeypatch.setattr("dokimi.repos.container.DockerRunner", lambda *a, **k: runner)
    out = tmp_path / "runs"
    args = build_parser().parse_args([
        "repo", "run", f"{root}@HEAD", "--manifest", ".agent-eval/repo-eval.yaml", "--harness", str(profile),
        "--bin", str(exe), "--model", "gpt-x", "--batch", "b1", "--out", str(out),
        "--cache-root", str(out / "caches"), "--agent-network", "host", "--task", "review_add",
        "--arm", "guidance:raw", "--tools", tools])
    args.func(args)
    return next((out / "b1").glob("review_add__*/")), runner


@pytest.mark.parametrize("rc", [1, 0])  # 0: only turn.failed marks the run as failed
@_POSIX_FAKE_EXE
def test_repo_run_keeps_a_failed_native_review_without_a_findings_block(tmp_path, monkeypatch, rc):
    assert FAKE_CODEX_REVIEW_FAILED.count("sys.exit(1)") == 1  # the template's exit line, so the swap below is not a no-op
    rd, _ = _run_codex_review(tmp_path, monkeypatch, FAKE_CODEX_REVIEW_FAILED.replace("sys.exit(1)", f"sys.exit({rc})"))
    assert (rd / "answer.txt").read_text() == "Review was interrupted. Please re-run /review and wait for it to complete."
    assert json.loads((rd / "result.json").read_text())["agent"]["is_error"] is True


# A review that prints a complete finding, then exits non-zero (or is killed by the timeout): no turn.failed.
FAKE_CODEX_REVIEW_EXIT_1 = r'''#!/usr/bin/env python3
import json, sys
if sys.argv[1:] == ["--version"]:
    print("codex-cli 0.160.0"); sys.exit(0)
text = "One bug.\n\nReview comment:\n\n- [P1] add() subtracts — src/calc.py:2-2\n  The change turns a + b into a - b."
print(json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": text}}))
print(json.dumps({"type": "turn.completed", "usage": {"input_tokens": 0, "output_tokens": 0}}))
sys.exit(1)
'''


@_POSIX_FAKE_EXE
def test_repo_run_gives_a_cut_off_review_no_findings_block_when_the_harness_exits_non_zero(tmp_path, monkeypatch):
    rd, _ = _run_codex_review(tmp_path, monkeypatch, FAKE_CODEX_REVIEW_EXIT_1)
    answer = (rd / "answer.txt").read_text()
    assert answer.startswith("One bug.") and "- [P1] add() subtracts" in answer  # the text stays, only the block goes
    assert "```json" not in answer
    # the harness exited 1 with no turn.failed record: the run shows the exit code, and is_error stays false
    agent = json.loads((rd / "result.json").read_text())["agent"]
    assert agent["rc"] == 1 and agent["is_error"] is False


@pytest.mark.parametrize("tools", ["read", "all"])
@_POSIX_FAKE_EXE
def test_repo_run_grades_a_native_review_answer(tmp_path, monkeypatch, tools):
    rd, runner = _run_codex_review(tmp_path, monkeypatch, FAKE_CODEX_REVIEW, tools)
    answer = (rd / "answer.txt").read_text()
    findings = json.loads(answer.rsplit("```json\n", 1)[1].split("\n```", 1)[0])
    assert findings[0]["file"] == "src/calc.py" and findings[0]["severity"] == "major"
    result = json.loads((rd / "result.json").read_text())
    assert result["harness"] == "codex-review" and result["grade"]["score"] == 1.0
    agent = next(s for s in runner.specs if "review" in s.argv)
    assert "Review HEAD." not in " ".join(agent.argv)  # the task prompt is not passed


@_POSIX_FAKE_EXE
def test_repo_run_grades_a_finding_from_an_earlier_agent_message(tmp_path, monkeypatch):
    # the findings sit in an earlier agent message; the last one only says the review is done
    later = '{"type": "item.completed", "item": {"type": "agent_message", "text": "Review finished."}}'
    fake = FAKE_CODEX_REVIEW.replace('print(json.dumps({"type": "turn.completed"',
                                     f"print(json.dumps({later}))\nprint(json.dumps({{\"type\": \"turn.completed\"", 1)
    rd, _ = _run_codex_review(tmp_path, monkeypatch, fake)
    answer = (rd / "answer.txt").read_text()
    assert answer.startswith("Review finished.")
    findings = json.loads(answer.rsplit("```json\n", 1)[1].split("\n```", 1)[0])
    assert findings[0]["file"] == "src/calc.py" and findings[0]["severity"] == "major"


def _run_fake(tmp_path, monkeypatch, *, companion: bool, link: bool = False):
    from repo_eval_fixture import PROFILE, fake_harness

    root, _ = make_repo(tmp_path)
    exe, _ = fake_harness(tmp_path)
    if companion:
        write(exe.parent, "fake-helper", "")
    if link:  # npm's layout: --bin is a link in another directory, with no companion beside it
        (tmp_path / "links").mkdir()
        (tmp_path / "links" / exe.name).symlink_to(exe)
        exe = tmp_path / "links" / exe.name
    profile = tmp_path / "fake-companion.yaml"
    profile.write_text(yaml.safe_dump({**PROFILE, "bin_companions": ["fake-helper"]}), encoding="utf-8")
    runner = FakeRunner()
    monkeypatch.setattr("dokimi.repos.container.DockerRunner", lambda *a, **k: runner)
    monkeypatch.setenv("FAKE_ACTION", "fix")
    out = tmp_path / "runs"
    args = build_parser().parse_args([
        "repo", "run", f"{root}@HEAD", "--manifest", ".agent-eval/repo-eval.yaml", "--harness", str(profile),
        "--bin", str(exe), "--model", "m", "--batch", "b1", "--out", str(out), "--cache-root", str(out / "caches"),
        "--agent-network", "host", "--task", "fix_add", "--arm", "guidance:raw"])
    return runner, out, args


@_POSIX_FAKE_EXE
def test_bin_companions_are_mounted_beside_the_harness(tmp_path, monkeypatch):
    from dokimi.repos.container import HARNESS_DIR

    runner, out, args = _run_fake(tmp_path, monkeypatch, companion=True)
    args.func(args)
    agent = next(s for s in runner.specs if "-p" in s.argv)
    mounts = {m.container: m for m in agent.mounts}
    assert mounts[f"{HARNESS_DIR}/fake-helper"].read_only and mounts[f"{HARNESS_DIR}/fake-agent"].read_only
    assert next((out / "b1").glob("*/result.json")).is_file()


@_POSIX_FAKE_EXE
def test_bin_companions_are_taken_from_beside_the_file_a_linked_bin_points_to(tmp_path, monkeypatch):
    from dokimi.repos.container import HARNESS_DIR

    runner, out, args = _run_fake(tmp_path, monkeypatch, companion=True, link=True)
    args.func(args)
    agent = next(s for s in runner.specs if "-p" in s.argv)
    mounts = {m.container: m.host for m in agent.mounts}
    assert mounts[f"{HARNESS_DIR}/fake-helper"] == (tmp_path / "bin" / "fake-helper").resolve()
    assert next((out / "b1").glob("*/result.json")).is_file()


@_POSIX_FAKE_EXE
def test_harness_mounts_takes_the_companion_from_beside_the_link_target(tmp_path):
    """Directly, so an unresolved ``harness_bin`` reaches the code (``repo run`` resolves it first)."""
    from dokimi.repos.container import HARNESS_DIR
    from dokimi.repos.runner import AgentOptions, harness_mounts
    from repo_eval_fixture import PROFILE

    real_dir, link_dir = tmp_path / "native", tmp_path / "links"
    real_dir.mkdir()
    link_dir.mkdir()
    write(real_dir, "fake-agent", "")
    write(real_dir, "fake-helper", "")
    (link_dir / "fake-agent").symlink_to(real_dir / "fake-agent")  # no companion beside the link
    profile = parse_profile({**PROFILE, "bin_companions": ["fake-helper"]})
    opts = AgentOptions(profile=profile, model="m", harness_bin=link_dir / "fake-agent")
    mounts = {m.container: m.host for m in harness_mounts(opts)}
    assert mounts[f"{HARNESS_DIR}/fake-helper"] == (real_dir / "fake-helper").resolve()
    assert mounts[f"{HARNESS_DIR}/fake-agent"] == (real_dir / "fake-agent").resolve()


def test_execution_id_changes_with_the_bytes_of_a_companion(tmp_path):
    from dokimi.repos.runner import AgentOptions, execution_record
    from repo_eval_fixture import PROFILE

    write(tmp_path, "fake-agent", "")
    write(tmp_path, "fake-helper", "v1")
    profile = parse_profile({**PROFILE, "bin_companions": ["fake-helper"]})
    opts = AgentOptions(profile=profile, model="m", harness_bin=tmp_path / "fake-agent")
    before = execution_record(opts)["id"]
    write(tmp_path, "fake-helper", "v2")  # same name, same executable: only the helper's bytes differ
    assert execution_record(opts)["id"] != before


@_POSIX_FAKE_EXE
def test_execution_id_follows_a_linked_bin_to_the_companion_beside_its_target(tmp_path):
    from dokimi.repos.runner import AgentOptions, execution_record
    from repo_eval_fixture import PROFILE

    real_dir, link_dir = tmp_path / "native", tmp_path / "links"
    write(real_dir, "fake-agent", "")
    write(real_dir, "fake-helper", "v1")
    link_dir.mkdir()
    (link_dir / "fake-agent").symlink_to(real_dir / "fake-agent")  # the companion is beside the target only
    profile = parse_profile({**PROFILE, "bin_companions": ["fake-helper"]})
    opts = AgentOptions(profile=profile, model="m", harness_bin=link_dir / "fake-agent")
    before = execution_record(opts)["id"]
    write(real_dir, "fake-helper", "v2")
    assert execution_record(opts)["id"] != before


@_POSIX_FAKE_EXE
def test_a_missing_bin_companion_stops_before_any_agent_runs(tmp_path, monkeypatch, capsys):
    runner, out, args = _run_fake(tmp_path, monkeypatch, companion=False)
    with pytest.raises(SystemExit):
        args.func(args)
    assert "needs fake-helper beside its executable" in capsys.readouterr().err
    assert not any("-p" in s.argv for s in runner.specs) and not list(out.glob("b1/*/result.json"))


@pytest.mark.parametrize("name", ["codex", "codex-review"])
def test_the_codex_profiles_pass_the_key_variable_into_the_agent(name):
    assert "CODEX_API_KEY" in load_profile(name).env_allow  # a comment naming it would not forward it


@pytest.mark.parametrize("name", ["codex", "codex-review"])
def test_the_codex_profiles_tell_a_provider_in_config_files_to_name_its_key_variable(name):
    """``codex exec`` sends no Authorization header for a provider without ``env_key`` (401 "Missing bearer").

    The advice is a comment in each profile (the profiles ship no config_files: a provider is the user's choice)
    and a section of the contract, so this checks the text of both.
    """
    assert 'env_key = "CODEX_API_KEY"' in Path(load_profile(name).path).read_text(encoding="utf-8")
    contract = (Path(__file__).resolve().parents[1] / "docs/repo-evaluation-contract.md").read_text(encoding="utf-8")
    assert 'env_key = "CODEX_API_KEY"' in contract.replace("`", "")


def _block(out):
    return json.loads(out.rsplit("```json\n", 1)[1].split("\n```", 1)[0])


_GOOD = "- [P1] t — /w/a.py:3\n  body\n"
_OTHER = "- [P2] u — /w/b.py:4\n  more\n"


def test_convert_answer_ignores_a_priority_line_in_an_earlier_message_with_no_finding():
    out = convert_answer("codex-review", _GOOD, "/w", messages=["[P1] means a blocker; listing next.", _GOOD])
    assert [f["file"] for f in _block(out)] == ["a.py"]


def test_convert_answer_counts_a_finding_repeated_in_two_messages_once():
    assert len(_block(convert_answer("codex-review", _GOOD, "/w", messages=[_GOOD, _GOOD]))) == 1


def test_convert_answer_the_final_findings_decide():
    # the final message holds the review: a finding only an earlier draft had is not graded
    assert [f["file"] for f in _block(convert_answer("codex-review", _GOOD, "/w", messages=[_OTHER, _GOOD]))] == [
        "a.py"]


@pytest.mark.parametrize("final", ["No issues found.", "Reviewed HEAD. No issues found.", "no issues\nDone",
                                   "The patch is correct."])
def test_convert_answer_a_final_clean_verdict_takes_back_an_earlier_draft(final):
    draft = "- [P1] Maybe leak \u2014 /w/a.c:2\n  thinking"
    assert _block(convert_answer("codex-review", final, "/w", messages=[draft, final])) == []


@pytest.mark.parametrize("final", ["No issues found beyond the leak above.", "Review finished.",
                                   "The patch is incorrect.", "No other issues."])
def test_convert_answer_a_final_message_with_no_verdict_keeps_the_earlier_findings(final):
    # only a whole clean-verdict sentence takes a finding back; a summary or a qualified sentence points at it
    out = _block(convert_answer("codex-review", final, "/w", messages=[_GOOD, final]))
    assert [f["file"] for f in out] == ["a.py"]


@pytest.mark.parametrize("tail", [[], ["Review finished."]], ids=["final", "summary-after"])
@pytest.mark.parametrize(("draft", "final", "want"), [
    ("- [P2] Leak — /w/a.py:3\n  body\n", "- [P1] Leak — /w/a.py:3-5\n  body\n", ("major", 5, "body")),
    ("- [P1] Leak — /w/a.py:3\n  draft\n", "- [P1] Leak — /w/a.py:3\n  final\n", ("major", 3, "final")),
    ("- [P1] Leak — /w/a.py:3\n  body\n", "- [P3] Leak — /w/a.py:3\n  body\n", ("minor", 3, "body")),
], ids=["priority-and-range", "body", "downgrade"])
def test_convert_answer_keeps_the_last_version_of_a_revised_finding(draft, final, want, tail):
    # the grader counts blocker/major findings: an earlier draft's priority must not stand for the final one's
    messages = [draft, final, *tail]
    out = _block(convert_answer("codex-review", messages[-1], "/w", messages=messages))
    assert [(f["severity"], f["end_line"], f["message"]) for f in out] == [want]


@pytest.mark.parametrize(("messages", "files"), [([_GOOD + _GOOD], ["a.py"]), (["intro", _GOOD + _GOOD], ["a.py"]),
                                                 ([_OTHER, _GOOD + _GOOD, "done"], ["b.py", "a.py"])])
def test_convert_answer_keeps_the_block_of_a_finding_repeated_in_one_message(messages, files):
    out = convert_answer("codex-review", messages[-1], "/w", messages=messages)
    assert "```json" in out  # a repeated finding is parsed, not format drift
    assert [f["file"] for f in _block(out)] == files


def test_convert_answer_reads_findings_from_every_message_once_each():
    out = convert_answer("codex-review", "done", "/w", messages=["", _GOOD, _OTHER, "done"])
    assert [f["file"] for f in _block(out)] == ["a.py", "b.py"]


def test_convert_answer_with_a_drifted_last_message_gets_no_block():
    last = "- [P1] t no path\n"
    assert convert_answer("codex-review", last, "/w", messages=["intro", last]) == last


@pytest.mark.parametrize("text", ["x\n  - [P1] title no path\n", "- **[P1]** t — /w/a.py:3\n",
                                  "1. [P1] Leak — /ws/a.c:2\n   body\n", "Findings:\n2) [P2] t no path\n"],
                         ids=["indented-no-path", "bold", "numbered-header", "numbered-paren-no-path"])
def test_convert_answer_counts_an_unparsed_header_in_any_shape(text):
    assert convert_answer("codex-review", text, "/w") == text  # a marker with no finding: no block


def test_convert_answer_a_numbered_header_after_a_finding_ends_its_body():
    # a numbered list item is a marker too: B is withheld, not swallowed as body text of A
    text = "- [P1] A — /w/a.py:3\n  body\n  2. [P2] B - /w/b.py:4\n    body\n"
    assert convert_answer("codex-review", text, "/w") == text


def test_convert_answer_counts_a_drifted_finding_in_an_earlier_message():
    # a hyphen, not an em dash: the bullet is a finding marker even in an earlier message with none parsed
    drift = "- [P1] Leak - a.c:2\n  body\n"
    assert convert_answer("codex-review", "done", "/w", messages=[drift, "done"]) == "done"
