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
