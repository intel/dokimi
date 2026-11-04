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
SKILL_MD = Path(__file__).parent / "fixtures" / "fidelity" / "skill" / "sum-fast" / "SKILL.md"
REQUIRED = os.environ.get("DOKIMI_FIDELITY_REQUIRED") == "1"
IMAGE = os.environ.get("DOKIMI_FIDELITY_IMAGE") or None


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
    out = fd.normalize_text(text, workdir="/opt/fidelity-x/work")
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
    actual = fd.capture_arm(case, binary=binary, scratch=tmp_path, image=IMAGE,
                            uid=os.getuid(), gid=os.getgid())
    skill_text = SKILL_MD.read_text(encoding="utf-8")
    leaks = fd.leaks_against(actual, golden, allowed=[fd.FIXTURE_INSTRUCTION, skill_text])
    assert [lk.as_dict() for lk in leaks] == []
    assert fd.diff_requests(actual, golden) == [], "\n".join(fd.diff_requests(actual, golden))


# -- the runners and the recording server, without a real client -------------------------

FAKE_CLIENT = """#!PYTHON
import json, os, urllib.request
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


def test_messages_request_refuses_a_client_with_no_tools_request():
    with pytest.raises(ValueError):
        fd.messages_request([{"path": "/v1/messages", "body": {"stream": True}}])


def test_capture_arm_runs_the_arm_argv_through_a_client(tmp_path):
    binary = _fake_client(tmp_path / "bin")
    request = fd.capture_arm("skill", binary=binary, scratch=tmp_path / "scratch")
    assert [tool["name"] for tool in request["tools"]] == ["Bash"]
    assert request["model"] == "<MODEL>"
    assert request["messages"][0]["content"][0]["text"] == fd.FIXTURE_INSTRUCTION
    with pytest.raises(ValueError):
        fd.capture_arm("nope", binary=binary, scratch=tmp_path / "other")


def test_run_host_gives_the_client_only_the_fixed_environment(tmp_path, monkeypatch):
    seen: dict = {}
    monkeypatch.setattr(fd.process, "run", lambda argv, **kw: seen.update(argv=list(argv), **kw))
    fd.run_host("/bin/claude", ["-p", "x"], workdir=tmp_path, scratch=tmp_path / "s")("http://127.0.0.1:9")
    assert seen["argv"] == ["/bin/claude", "-p", "x"]
    assert seen["env"].allow == ()
    assert seen["env"].set["HOME"] == str(tmp_path / "s" / "home")
    assert seen["env"].set["ANTHROPIC_BASE_URL"] == "http://127.0.0.1:9"


def test_run_docker_builds_the_container_command(tmp_path, monkeypatch):
    seen: dict = {}
    monkeypatch.setattr(fd.process, "run", lambda argv, **kw: seen.update(argv=list(argv), **kw))
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
    assert seen["env"].allow is None  # the docker client inherits the parent environment


def test_golden_round_trip_and_diff(tmp_path):
    request = {"model": "<MODEL>", "tools": []}
    path = tmp_path / "g" / "bare.json"
    fd.write_golden(path, request, version="2.1.318", case="bare")
    doc = fd.load_golden(path)
    assert doc["_meta"]["case"] == "bare" and doc["request"] == request
    assert fd.diff_requests(request, doc["request"]) == []
    assert any(line.startswith(("-", "+")) for line in fd.diff_requests({"model": "<MODEL>", "tools": [1]}, doc["request"]))
