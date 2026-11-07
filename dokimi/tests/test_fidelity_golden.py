# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Fidelity contract: a docker-claude arm sends what the pinned Claude Code sends.

The arm's argv comes from its own builder; the request is recorded from a real ``claude``
(see :mod:`dokimi.harnesses.fidelity`) and compared with the golden in
``tests/fixtures/fidelity``. The golden tests need the pinned binary on PATH: they skip
without it, and fail instead when ``DOKIMI_FIDELITY_REQUIRED=1`` (the CI job sets it).
``DOKIMI_FIDELITY_IMAGE`` runs the client in that container image, as the golden is taken.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from dokimi.harnesses import fidelity as fd
from dokimi.harnesses.payload_audit import audit_payload, markers_for

GOLDEN_DIR = Path(__file__).parent / "fixtures" / "fidelity" / f"claude-code-{fd.PINNED_CLAUDE_VERSION}"
SKILL_DIR = Path(__file__).parents[1] / fd.FIXTURE_SKILL_REL
SKILL_MD = SKILL_DIR / "SKILL.md"
REQUIRED = os.environ.get("DOKIMI_FIDELITY_REQUIRED") == "1"
IMAGE = os.environ.get("DOKIMI_FIDELITY_IMAGE") or None
#: Set on a maintainer's host whose native build adds its own skills; CI never sets it.
OPERATOR_HOST = os.environ.get("DOKIMI_FIDELITY_OPERATOR_HOST") == "1"


def _pinned_binary():
    binary = shutil.which("claude")
    if not binary:
        return None
    out = subprocess.run([binary, "--version"], capture_output=True, text=True, timeout=60, check=False)
    return binary if out.stdout.split(" ")[0] == fd.PINNED_CLAUDE_VERSION else None


def test_normalization_blanks_run_specific_text():
    text = ("Primary working directory: /opt/fidelity-x/work\n - Is a git repository: true\n"
            "Today's date is 2026-11-04. cc_version=2.1.318.075; msg_01AbCdEfGhIj0123456789ab "
            "claude-haiku-4-5 named Haiku 4.5 Platform: linux")
    out = fd.normalize_text(text, workdir="/opt/fidelity-x/work", model="claude-haiku-4-5")
    assert "<WORKDIR>" in out and "<DATE>" in out and "<VERSION>" in out
    assert "<ID>" in out and "<MODEL>" in out and "<HOST>" in out and "<GIT>" in out
    assert fd.residue(out) == []


def test_operator_reminders_and_other_skills_are_not_part_of_the_golden():
    record = {"path": "/v1/messages", "headers": {}, "body": {
        "system": [{"type": "text", "text": "You are an agent.\n\n<total_tokens>1 left</total_tokens>"}],
        "tools": [{"name": "Skill"}],
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": "<system-reminder>\nThe following skills are available for use with the Skill tool:\n\n- sum-fast:sum-fast: Sum.\n- dataviz: Charts.\n</system-reminder>"},
            {"type": "text", "text": "<system-reminder>\nAttribution for git commits: do X.\n</system-reminder>"},
            {"type": "text", "text": "Fix the off-by-one in sum()."},
        ]}]}}
    req = fd.normalize_request(record, keep_skills=("sum-fast",))
    text = json.dumps(req)
    assert "dataviz" not in text and "Attribution" not in text and "total_tokens" not in text
    assert "sum-fast:sum-fast" in text and "Fix the off-by-one in sum()." in text


@pytest.mark.parametrize("case", fd.CASES)
def test_golden_is_pinned_and_free_of_run_specific_text(case):
    doc = fd.load_golden(GOLDEN_DIR / f"{case}.json")
    assert doc["_meta"]["claude_code_version"] == fd.PINNED_CLAUDE_VERSION
    assert fd.residue(doc["request"]) == []
    names = [t["name"] for t in doc["request"]["tools"]]
    assert "Skill" in names and "Bash" in names
    listing = json.dumps(doc["request"]["messages"])
    assert ("sum-fast:sum-fast" in listing) == (case == "skill")
    assert all(mark in json.dumps(doc["request"]) for mark in fd.OPERATOR_REMINDERS)  # compared as sent


def test_the_audit_is_live_on_an_arm_request():
    golden = fd.load_golden(GOLDEN_DIR / "bare.json")["request"]
    clean = json.loads(json.dumps(golden))
    assert fd.leaks_against(clean, golden) == []
    planted = json.loads(json.dumps(golden))
    planted["messages"][0]["content"].append({"type": "text", "text": "expected_skill: sum-fast"})
    assert {lk.marker for lk in fd.leaks_against(planted, golden)} == {"expected_skill"}
    assert audit_payload(planted, markers_for(arm_names=["docker-claude"]), allowed=[])


def test_arm_argv_keeps_the_sealed_flags(tmp_path):
    from dokimi.harnesses.docker_solver import DockerSolverHarness
    from dokimi.harnesses.tasks import TaskSpec

    task = TaskSpec(name="fidelity-sum", path=tmp_path, instruction=fd.FIXTURE_INSTRUCTION)
    harness = DockerSolverHarness(harness_id="docker-claude", skill_notice=False)
    argv = fd.arm_argv(harness, task, model=fd.FIXTURE_MODEL, output_dir=tmp_path)
    assert argv[:2] == ["-p", fd.FIXTURE_INSTRUCTION]
    for flag in ("--setting-sources", "--strict-mcp-config", "--no-session-persistence"):
        assert flag in argv
    assert argv[argv.index("--setting-sources") + 1] == ""


@pytest.mark.parametrize("case", fd.CASES)
def test_docker_claude_payload_matches_golden(case, tmp_path):
    binary = _pinned_binary()
    if binary is None:
        if REQUIRED:
            pytest.fail(f"claude {fd.PINNED_CLAUDE_VERSION} is not on PATH but DOKIMI_FIDELITY_REQUIRED=1")
        pytest.skip(f"claude {fd.PINNED_CLAUDE_VERSION} is not on PATH")
    golden = fd.load_golden(GOLDEN_DIR / f"{case}.json")["request"]
    golden = fd.operator_view(golden) if OPERATOR_HOST else golden
    ids = {"uid": os.getuid(), "gid": os.getgid()} if IMAGE else {}  # container captures only
    actual = fd.capture_arm(case, binary=binary, scratch=tmp_path, image=IMAGE, skill_dir=SKILL_DIR,
                            operator_host=OPERATOR_HOST, **ids)
    skill_text = SKILL_MD.read_text(encoding="utf-8")
    leaks = fd.leaks_against(actual, golden, allowed=[fd.FIXTURE_INSTRUCTION, skill_text])
    assert [lk.as_dict() for lk in leaks] == []
    assert fd.diff_requests(actual, golden) == [], "\n".join(fd.diff_requests(actual, golden))


# -- the runners and the recording server, without a real client -------------------------

FAKE_CLIENT = """#!PYTHON
import json, os, sys, urllib.request
# What the test asserts about the launch: the argv it got and the sealed skill switches.
seen = {"argv": sys.argv[1:], "env": {k: os.environ.get(k) for k in (
    "CLAUDE_CODE_DISABLE_BUNDLED_SKILLS", "CLAUDE_CODE_DISABLE_CLAUDE_API_SKILL",
    "CLAUDE_CODE_DISABLE_POLICY_SKILLS")}}
if os.environ.get("CLAUDE_CONFIG_DIR"):
    with open(os.path.join(os.environ["CLAUDE_CONFIG_DIR"], "launch.json"), "w", encoding="utf-8") as fh:
        json.dump(seen, fh)
base = os.environ["ANTHROPIC_BASE_URL"]
main = {"model": "claude-haiku-4-5", "stream": False,
        "system": [{"type": "text", "text": "You are an agent."}],
        "tools": [{"name": "Bash", "input_schema": {"type": "object"}}],
        "messages": [{"role": "user", "content": [{"type": "text", "text": "Fix the off-by-one in sum()."}]}]}
side = {"model": "claude-haiku-4-5", "stream": True, "messages": [{"role": "user", "content": "title"}]}
for body in (main, side):
    req = urllib.request.Request(base + "/v1/messages", data=json.dumps(body).encode(),
                                 headers={"content-type": "application/json", "x-app": "cli"}, method="POST")
    urllib.request.urlopen(req, timeout=30).read()
"""


def _fake_client(directory: Path) -> str:
    directory.mkdir(parents=True, exist_ok=True)
    script = directory / "fake-claude"
    script.write_text(FAKE_CLIENT.replace("PYTHON", sys.executable), encoding="utf-8")
    script.chmod(0o755)
    return str(script)


def test_record_keeps_every_request_and_the_main_turn(tmp_path):
    binary = _fake_client(tmp_path)
    env = {"PATH": os.environ.get("PATH", ""), "ANTHROPIC_BASE_URL": ""}

    def run(base_url: str) -> None:
        subprocess.run([binary], env={**env, "ANTHROPIC_BASE_URL": base_url}, check=True, timeout=60)

    records = fd.record(run)
    assert [rec["path"] for rec in records] == ["/v1/messages", "/v1/messages"]
    assert fd.messages_request(records)["body"]["tools"][0]["name"] == "Bash"
    assert records[1]["body"]["stream"] is True


def test_capture_arm_refuses_a_request_for_another_model(tmp_path, monkeypatch):
    # The model is blanked in the golden, so a client that sends another one is refused here.
    other = _fake_client(tmp_path / "bin")
    script = Path(other)
    script.write_text(script.read_text(encoding="utf-8").replace(
        'main = {"model": "claude-haiku-4-5"', 'main = {"model": "claude-sonnet-4-6"'), encoding="utf-8")
    with pytest.raises(ValueError, match="claude-sonnet-4-6"):
        fd.capture_arm("bare", binary=other, scratch=tmp_path / "scratch")


def test_messages_request_refuses_a_client_with_no_tools_request():
    with pytest.raises(ValueError):
        fd.messages_request([{"path": "/v1/messages", "body": {"stream": True}}])


def test_capture_arm_runs_the_arm_argv_through_a_client(tmp_path):
    binary = _fake_client(tmp_path / "bin")
    scratch = tmp_path / "scratch"
    request = fd.capture_arm("skill", binary=binary, scratch=scratch, skill_dir=SKILL_DIR)
    assert [tool["name"] for tool in request["tools"]] == ["Bash"]
    assert request["model"] == "<MODEL>"
    assert request["messages"][0]["content"][0]["text"] == fd.FIXTURE_INSTRUCTION
    with pytest.raises(ValueError):
        fd.capture_arm("nope", binary=binary, scratch=tmp_path / "other")


def test_capture_arm_passes_the_arm_prompt_skill_and_seal_to_the_client(tmp_path):
    # The fake client records what it was launched with: the prompt, the sealed flags and
    # the skill plugin must reach the client, not only be built by the arm's argv builder.
    binary = _fake_client(tmp_path / "bin")
    scratch = tmp_path / "scratch"
    fd.capture_arm("skill", binary=binary, scratch=scratch, skill_dir=SKILL_DIR)
    launch = json.loads((scratch / "home" / ".claude" / "launch.json").read_text(encoding="utf-8"))
    argv = launch["argv"]
    assert any(fd.FIXTURE_INSTRUCTION in arg for arg in argv)  # the task is inside the -p prompt
    assert "--dangerously-skip-permissions" in argv
    assert argv[argv.index("--setting-sources") + 1] == ""
    assert argv[argv.index("--plugin-dir") + 1] == str(scratch / "plugin")
    assert launch["env"] == {"CLAUDE_CODE_DISABLE_BUNDLED_SKILLS": "1",
                             "CLAUDE_CODE_DISABLE_CLAUDE_API_SKILL": "1",
                             "CLAUDE_CODE_DISABLE_POLICY_SKILLS": "1"}


def test_run_host_gives_the_client_only_the_fixed_environment(tmp_path, monkeypatch):
    seen: dict = {}
    monkeypatch.setattr(fd.process, "run",
                        lambda argv, **kw: seen.update(argv=list(argv), **kw) or fd.process.reply())
    fd.run_host("/bin/claude", ["-p", "x"], workdir=tmp_path, scratch=tmp_path / "s")("http://127.0.0.1:9")
    assert seen["argv"] == ["/bin/claude", "-p", "x"]
    assert seen["env"].allow == ()
    assert seen["env"].set["HOME"] == str(tmp_path / "s" / "home")
    assert seen["env"].set["ANTHROPIC_BASE_URL"] == "http://127.0.0.1:9"
    assert seen["env"].set["CLAUDE_CONFIG_DIR"] == str(tmp_path / "s" / "home" / ".claude")


def test_run_docker_builds_the_container_command(tmp_path, monkeypatch):
    seen: dict = {}
    monkeypatch.setattr(fd.process, "run",
                        lambda argv, **kw: seen.update(argv=list(argv), **kw) or fd.process.reply())
    runner = fd.run_docker("img:1", "/bin/claude", ["-p", "x"], workdir=tmp_path,
                           plugin_dir=tmp_path / "plugin", uid=1001, gid=1002)
    runner("http://127.0.0.1:9")
    argv = seen["argv"]
    assert argv[:5] == ["docker", "run", "--rm", "--network", "host"]
    assert "/bin/claude:/opt/agent/claude:ro" in argv
    assert f"{tmp_path}:{tmp_path}" in argv
    assert f"{tmp_path / 'plugin'}:{tmp_path / 'plugin'}:ro" in argv
    assert "1001:1002" in argv and "ANTHROPIC_BASE_URL=http://127.0.0.1:9" in argv
    assert argv[-4:] == ["img:1", "/opt/agent/claude", "-p", "x"]
    assert "CLAUDE_CONFIG_DIR=/home/agent/.claude" in argv  # container_agent's layout
    assert seen["env"].allow is None  # the docker client inherits the parent environment


def test_the_package_reads_no_path_outside_itself(tmp_path):
    # fidelity.py ships in the wheel and tests/ does not: the skill dir is the caller's.
    assert not hasattr(fd, "FIXTURE_SKILL")
    assert SKILL_MD.is_file()
    with pytest.raises(ValueError, match="skill_dir"):
        fd.capture_arm("skill", binary="/bin/false", scratch=tmp_path)


def test_golden_round_trip_and_diff(tmp_path):
    request = {"model": "<MODEL>", "tools": []}
    path = tmp_path / "g" / "bare.json"
    fd.write_golden(path, request, version="2.1.318", case="bare")
    doc = fd.load_golden(path)
    assert doc["_meta"]["case"] == "bare" and doc["request"] == request
    assert fd.diff_requests(request, doc["request"]) == []
    assert any(line.startswith(("-", "+")) for line in fd.diff_requests({"model": "<MODEL>", "tools": [1]}, doc["request"]))


def _record_with(text: str, headers: dict | None = None) -> dict:
    body = {"model": "claude-haiku-4-5", "messages": [{"role": "user", "content": [{"type": "text", "text": text}]}]}
    return {"path": "/v1/messages", "headers": headers or {}, "body": body}


def _texts(request: dict) -> str:
    return "\n".join(block["text"] for msg in request["messages"] for block in msg["content"])


def test_excluded_skill_entries_leave_no_continuation_lines():
    listing = ("The following skills are available for use with the Skill tool:\n\n"
               "- sum-fast:sum-fast: Sum.\n"
               "- other: Description.\nContinuation instruction\nTRIGGER: use for plotting.\n"
               "- dataviz: Charts.\nTRIGGER: use for plotting.\n\nAfter the listing.")
    out = _texts(fd.normalize_request(_record_with(listing), keep_skills=("sum-fast",)))
    assert "sum-fast:sum-fast: Sum." in out
    assert "other" not in out and "Continuation" not in out and "TRIGGER" not in out
    assert "dataviz" not in out and "After the listing." in out


def test_operator_reminder_keeps_the_other_text_of_its_block():
    bare = "Attribution for git commits: X\nexpected_skill: sum-fast"
    wrapped = ("<system-reminder>\nAttribution for git commits: X\n</system-reminder>\n"
               "<system-reminder>\nexpected_skill: sum-fast\n</system-reminder>")
    for text in (bare, wrapped):
        out = _texts(fd.normalize_request(_record_with(text), keep_skills=("sum-fast",)))
        assert "expected_skill: sum-fast" in out and "Attribution" not in out


def test_a_block_that_is_only_an_operator_reminder_is_dropped():
    for text in ("<total_tokens>5000 left</total_tokens>",
                 "<system-reminder>\n- End git commit messages with:\nCo-Authored-By: X\n</system-reminder>"):
        out = fd.normalize_request(_record_with(text), keep_skills=("sum-fast",))
        assert out["messages"][0]["content"] == []


def test_a_clean_capture_keeps_the_clients_reminders_for_the_audit():
    record = _record_with("<system-reminder>\nAttribution for git commits:\n- End git commit messages with:\n"
                          "Reviewed-by: expected_skill\n</system-reminder>")
    record["body"]["system"] = [{"type": "text", "text": "You are an agent.\n\n<total_tokens>1 left</total_tokens>"}]
    out = fd.normalize_request(record)
    assert "<total_tokens>1 left</total_tokens>" in out["system"][0]["text"]
    assert "Reviewed-by: expected_skill" in _texts(out)
    golden = fd.load_golden(GOLDEN_DIR / "bare.json")["request"]
    assert "expected_skill" in {lk.marker for lk in fd.leaks_against(out, golden)}


def test_an_operator_host_capture_is_compared_without_the_reminders():
    golden = fd.load_golden(GOLDEN_DIR / "bare.json")["request"]
    own = json.loads(json.dumps(golden).replace("15000000 tokens left", "999 tokens left"))
    record = {"headers": own.pop("headers"), "body": own}
    capture = fd.normalize_request(record, keep_skills=(fd.FIXTURE_SKILL_NAME,))
    assert fd.diff_requests(capture, fd.operator_view(golden)) == []
    assert fd.diff_requests(fd.normalize_request(record), golden) != []


def test_residue_allows_only_the_attribution_reminders_fixed_address():
    assert fd.residue({"x": "Co-Authored-By: Claude <noreply@anthropic.com>"}) == []
    assert fd.residue({"x": "Co-Authored-By: Bob <bob@example.com>"})


def test_billing_version_suffix_is_blanked_in_full():
    assert fd.normalize_text("cc_version=2.1.318.07b; cch=1") == "cc_version=<VERSION>; cch=1"
    assert fd.normalize_text("cc_version=2.1.318.07") == "cc_version=<VERSION>"
    assert fd.residue({"x": fd.normalize_text("cc_version=2.1.318.07b")}) == []


def test_api_version_and_beta_are_compared_raw():
    def headers(version: str, beta: str) -> dict:
        return {"anthropic-version": version, "anthropic-beta": beta}

    old = fd.normalize_request(_record_with("hi", headers("2023-06-01", "context-management-2025-06-27")))
    new = fd.normalize_request(_record_with("hi", headers("2024-06-01", "context-management-2025-10-01")))
    assert old["headers"]["anthropic-version"] == "2023-06-01"
    assert old["headers"] != new["headers"]
    assert fd.diff_requests(new, old) != []


def test_a_dated_beta_named_after_a_model_is_compared_raw():
    def beta(name: str) -> dict:
        return {"anthropic-beta": f"a-2025-05-14,{name}"}

    old = fd.normalize_request(_record_with("hi", beta("claude-code-20250219")))
    new = fd.normalize_request(_record_with("hi", beta("claude-code-99999999")))
    assert fd.diff_requests(new, old) != []


def test_only_the_request_model_is_blanked_in_the_text():
    # The system prompt names the other models: a wrong id there must stay a diff.
    text = "The exact model ID is claude-haiku-4-5. Model IDs: Haiku 5.5: 'claude-haiku-5-5', Fable 5.1: 'claude-fable-5-1'."
    out = _texts(fd.normalize_request(_record_with(text)))
    assert "claude-haiku-4-5" not in out and "The exact model ID is <MODEL>." in out
    assert "'claude-haiku-5-5'" in out and "'claude-fable-5-1'" in out


def test_the_model_id_and_name_are_blanked_whole():
    out = fd.normalize_text("model ID is claude-haiku-4-5-20251001. named Haiku 4.5. The exact",
                            model="claude-haiku-4-5")
    assert out == "model ID is claude-haiku-4-5-20251001. named <MODEL_NAME>. The exact"
    assert fd.normalize_text("is claude-haiku-4-5.", model="claude-haiku-4-5") == "is <MODEL>."


def test_residue_flags_each_run_specific_pattern():
    dirty = {
        "date": "Today's date is 2026-11-04.",
        "month-year": "Knowledge cutoff: November 2026.",
        "slash-date": "saved 2026/11/05",
        "uuid": "id 0b1c2d3e-4f50-4a6b-8c7d-9e0f1a2b3c4d",
        "hex": "digest " + "ab" * 32,
        "version": "cc_version=2.1.318.07b",
        "abs-path": "cwd /usr/src/app",
        "workspace-path": "cwd /workspace/repo",
        "users-path": "cwd /Users/bob/x",
        "home-path": "cwd /home/bob/x",
        "email": "contact bob@intel.com",
        "model": "claude-haiku-4-5",
    }
    for name, text in dirty.items():
        assert fd.residue({"model": text} if name == "model" else {"x": text}), name
    assert fd.residue({"x": "clean text"}) == []


def test_excluded_only_listing_keeps_the_text_around_it():
    # An excluded-only listing used to drop the whole block, so the text after it (here an
    # expected-skill marker) never reached the comparison or the leak audit.
    listing = ("The following skills are available for use with the Skill tool:\n\n"
               "- other: Description.\n\nexpected_skill: sum-fast")
    out = _texts(fd.normalize_request(_record_with(listing), keep_skills=("sum-fast",)))
    assert "expected_skill: sum-fast" in out
    assert "other" not in out and "Description" not in out


def test_a_wrapper_left_by_an_excluded_only_listing_is_dropped():
    # Every entry excluded, nothing after the listing: the block is the listing's own wrapper and goes.
    listing = ("<system-reminder>\nThe following skills are available for use with the Skill tool:\n\n"
               "- dataviz: x\n- loop: y\n</system-reminder>")
    out = fd.normalize_request(_record_with(listing), keep_skills=("sum-fast",))
    assert out["messages"][0]["content"] == []


def test_operator_view_keeps_the_fixture_skill_and_drops_the_reminders():
    # operator_view is the golden side of the operator-host comparison: the fixture skill stays, the reminders go.
    golden = fd.load_golden(GOLDEN_DIR / "skill.json")["request"]
    view = fd.operator_view(golden)
    text = _texts(view)
    assert "\n- sum-fast:sum-fast:" in text  # the listing entry, not the prose that names the skill
    assert "<total_tokens>" not in json.dumps(view) and "End git commit messages with" not in text


def test_capture_arm_passes_the_operator_host_switch_to_normalization(tmp_path, monkeypatch):
    binary = _fake_client(tmp_path / "bin")
    kept = []
    real = fd.normalize_request

    def spy(record, **kw):
        kept.append(kw.get("keep_skills"))
        return real(record, **kw)

    monkeypatch.setattr(fd, "normalize_request", spy)
    fd.capture_arm("skill", binary=binary, scratch=tmp_path / "clean", skill_dir=SKILL_DIR)
    fd.capture_arm("skill", binary=binary, scratch=tmp_path / "host", skill_dir=SKILL_DIR, operator_host=True)
    assert kept == [None, (fd.FIXTURE_SKILL_NAME,)]


_LISTING = "The following skills are available for use with the Skill tool:\n\n"


def test_a_name_that_only_contains_a_kept_name_is_excluded():
    text = _LISTING + "- sum-fast:sum-fast: Sum.\n- other: use instead of sum-fast.\n- summarize: x\n"
    out = _texts(fd.normalize_request(_record_with(text), keep_skills=("sum-fast",)))
    assert "sum-fast:sum-fast: Sum." in out
    assert "other:" not in out and "summarize" not in out


def test_the_listing_ends_at_its_closing_tag():
    text = "<system-reminder>\n" + _LISTING + "- sum-fast:sum-fast: y\n- dataviz: x\n</system-reminder>\nafter"
    out = _texts(fd.normalize_request(_record_with(text), keep_skills=("sum-fast",)))
    assert "dataviz" not in out and "</system-reminder>" in out and "after" in out


def test_the_listing_is_compared_as_sent_unless_an_operator_host_is_named():
    # A clean capture that gains bundled skills is a broken seal: it must differ from the golden.
    golden = fd.load_golden(GOLDEN_DIR / "skill.json")["request"]
    sealed = {"path": "/v1/messages", "headers": {}, "body": json.loads(json.dumps(golden))}
    broken = json.loads(json.dumps(sealed))
    for block in broken["body"]["messages"][0]["content"]:
        if "sum-fast:sum-fast" in block.get("text", ""):
            block["text"] = block["text"].replace("- sum-fast:sum-fast", "- simplify: x\n- update-config: y\n- sum-fast:sum-fast")
    assert fd.diff_requests(fd.normalize_request(broken), fd.normalize_request(sealed)) != []
    assert fd.diff_requests(fd.normalize_request(broken, keep_skills=("sum-fast",)),
                            fd.normalize_request(sealed, keep_skills=("sum-fast",))) == []


def test_a_bullet_list_after_the_listing_is_kept():
    text = _LISTING + "- sum-fast:sum-fast: y\n- dataviz: x\n\nNotes:\n- Be concise"
    out = _texts(fd.normalize_request(_record_with(text), keep_skills=("sum-fast",)))
    assert "dataviz" not in out and "Notes:" in out and "- Be concise" in out


@pytest.mark.parametrize("failure", [{"returncode": 1, "stderr": "auth failed"},
                                     {"returncode": 124, "timed_out": True}])
def test_run_host_refuses_a_client_that_fails_after_it_posted(tmp_path, monkeypatch, failure):
    # A client that posts its request and then exits nonzero or times out must not be
    # recorded as a capture: the runner raises, so no golden is compared or refreshed.
    monkeypatch.setattr(fd.process, "run", lambda argv, **kw: fd.process.reply(**failure))
    runner = fd.run_host("/bin/claude", ["-p", "x"], workdir=tmp_path, scratch=tmp_path / "s")
    with pytest.raises(RuntimeError, match="/bin/claude"):
        runner("http://127.0.0.1:9")


def test_run_docker_refuses_a_client_that_fails_after_it_posted(tmp_path, monkeypatch):
    monkeypatch.setattr(fd.process, "run", lambda argv, **kw: fd.process.reply(returncode=1, stderr="boom"))
    runner = fd.run_docker("img:1", "/bin/claude", ["-p", "x"], workdir=tmp_path,
                           plugin_dir=None, uid=1001, gid=1002)
    with pytest.raises(RuntimeError, match="boom"):
        runner("http://127.0.0.1:9")
