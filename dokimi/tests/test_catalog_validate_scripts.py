# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Script checks of ``catalog check``: CRLF, syntax and the optional linters on the scripts a skill bundles.

The rules are in ``dokimi/external/script_checks.py``; these tests read them through ``review_skills``
and, for the linter and ``bash -n`` calls, through a :class:`process.FakeRunner` (nothing is spawned).
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest

from dokimi import process
from dokimi.external import script_checks
from dokimi.external.script_checks import script_findings
from tests.test_catalog_validate import _codes, _reviews_by_name, _skill

LINTERS = ("ruff", "shellcheck")
BASH = shutil.which("bash")
needs_bash = pytest.mark.skipif(BASH is None, reason="bash is not on PATH")


@pytest.fixture
def no_linters(monkeypatch):
    """ruff and shellcheck taken off PATH, so a box with them installed gives the same result as one without."""
    real = shutil.which
    monkeypatch.setattr(
        shutil, "which", lambda name, *a, **k: None if name in LINTERS else real(name, *a, **k)
    )


def _bundle(root: Path, files: dict[str, bytes]) -> Path:
    """A skill ``bundled`` under ``root/skills`` with ``files`` written beside its SKILL.md."""
    _skill(root / "skills", "bundled", name="bundled", description="Use for bundled work.")
    skill = root / "skills" / "bundled"
    for rel, data in files.items():
        (skill / rel).parent.mkdir(parents=True, exist_ok=True)
        (skill / rel).write_bytes(data)
    return skill


def _tools(*names: str):
    """A ``which`` that finds only ``names``, each at a fake absolute path."""
    return lambda name: f"/usr/bin/{name}" if name in names else None


def _linter_runner(ruff=None, shellcheck=None) -> process.FakeRunner:
    """A runner that answers ruff and shellcheck with the given JSON (exit 1: they found something)."""
    def handler(call):
        if call.argv[0].endswith("ruff") and ruff is not None:
            return process.reply(json.dumps(ruff), returncode=1)
        if call.argv[0].endswith("shellcheck") and shellcheck is not None:
            return process.reply(json.dumps(shellcheck), returncode=1)
        return None  # bash -n and anything else: exit 0, no output
    return process.FakeRunner(handler)


def test_a_shell_script_with_crlf_line_endings_is_an_error(tmp_path, no_linters):
    _bundle(tmp_path, {"run.sh": b"#!/bin/bash\r\necho hi\r\n"})

    review = _reviews_by_name(tmp_path)["bundled"]
    assert review.state == "error"
    assert _codes(review) == ["script-crlf"]


def test_an_extensionless_shebang_script_with_crlf_is_an_error(tmp_path, no_linters):
    _bundle(tmp_path, {"tool": b"#!/bin/sh\r\necho hi\r\n"})

    assert _codes(_reviews_by_name(tmp_path)["bundled"]) == ["script-crlf"]


def test_a_python_file_that_does_not_parse_is_an_error_with_its_line(tmp_path, no_linters):
    _bundle(tmp_path, {"tool.py": b"import os\n\ndef f(:\n    pass\n"})

    review = _reviews_by_name(tmp_path)["bundled"]
    assert _codes(review) == ["script-syntax"]
    assert review.findings[0].message.startswith("tool.py does not parse as Python: ")
    assert review.findings[0].message.endswith("(line 3)")


@needs_bash
def test_a_shell_script_bash_rejects_is_an_error(tmp_path, no_linters):
    _bundle(tmp_path, {"run.sh": b"#!/bin/bash\nif true; then\n  echo x\n"})  # no fi

    review = _reviews_by_name(tmp_path)["bundled"]
    assert _codes(review) == ["script-syntax"]
    assert review.findings[0].message.startswith("run.sh does not parse as shell: ")


def test_bash_n_gets_the_script_on_stdin_with_no_environment(tmp_path):
    skill = _bundle(tmp_path, {"run.sh": b"if true; then\n"})
    runner = process.FakeRunner(lambda call: process.reply(stderr="bash: line 2: syntax error\n", returncode=2))

    findings = script_findings(skill, which=_tools("bash"), runner=runner)

    assert findings == [("error", "script-syntax",
                         "run.sh does not parse as shell: bash: line 2: syntax error")]
    [call] = runner.calls
    assert call.argv == ("/usr/bin/bash", "-n")  # the script is never a file argument
    assert call.input == b"if true; then\n"
    assert call.env == {}  # no BASH_ENV, no PATH: nothing the script could source


def test_a_crlf_shell_script_is_reported_once_and_not_passed_to_bash(tmp_path):
    skill = _bundle(tmp_path, {"run.sh": b"echo a\r\nif true; then\r\n"})
    runner = process.FakeRunner()

    findings = script_findings(skill, which=_tools("bash"), runner=runner)

    assert [code for _, code, _ in findings] == ["script-crlf"]
    assert runner.calls == []


def test_ruff_and_shellcheck_counts_go_in_one_script_lint_note(tmp_path):
    skill = _bundle(tmp_path, {"tool.py": b"x = 1\n", "run.sh": b"#!/bin/sh\necho $x\n"})
    runner = _linter_runner(
        ruff=[{"code": "S602"}, {"code": "S602"}, {"code": "S607"}],
        shellcheck=[{"code": 2086}],
    )

    findings = script_findings(skill, which=_tools("ruff", "shellcheck", "bash"), runner=runner)

    assert findings == [("note", "script-lint",
                         "bundled-script lint: ruff 3 (S602 x2, S607 x1); shellcheck 1 (SC2086 x1)")]
    ruff_call = next(c for c in runner.calls if c.argv[0].endswith("ruff"))
    assert "--isolated" in ruff_call.argv and "--no-cache" in ruff_call.argv
    assert ruff_call.argv[ruff_call.argv.index("--select") + 1] == "S,E9,F63,F7,F82"
    assert all(c.env == {} for c in runner.calls if c.argv[0].endswith(("ruff", "shellcheck")))


def test_shellcheck_json_wrapped_in_an_object_is_read_too(tmp_path):
    skill = _bundle(tmp_path, {"run.sh": b"#!/bin/sh\necho $x\n"})
    runner = process.FakeRunner(lambda call: process.reply(json.dumps({"comments": [{"code": 2086}]}), returncode=1))

    findings = script_findings(skill, which=_tools("shellcheck"), runner=runner)

    assert findings == [("note", "script-lint", "bundled-script lint: shellcheck 1 (SC2086 x1)")]


def test_no_linter_on_path_adds_no_finding_and_spawns_nothing(tmp_path):
    skill = _bundle(tmp_path, {"tool.py": b"x = 1\n", "run.sh": b"echo hi\n"})
    runner = process.FakeRunner()

    assert script_findings(skill, which=_tools(), runner=runner) == []
    assert runner.calls == []


def test_a_linter_that_fails_to_run_adds_no_lint_note(tmp_path):
    skill = _bundle(tmp_path, {"tool.py": b"x = 1\n"})
    runner = process.FakeRunner(lambda call: process.reply("", stderr="error: unknown flag", returncode=2))

    assert script_findings(skill, which=_tools("ruff"), runner=runner) == []


def test_a_linter_that_prints_no_json_adds_no_lint_note(tmp_path):
    skill = _bundle(tmp_path, {"tool.py": b"x = 1\n"})
    runner = process.FakeRunner(lambda call: process.reply("not json", returncode=1))

    assert script_findings(skill, which=_tools("ruff"), runner=runner) == []


def test_python_with_crlf_and_no_shebang_is_fine(tmp_path, no_linters):
    _bundle(tmp_path, {"tool.py": b"x = 1\r\n"})

    assert _reviews_by_name(tmp_path)["bundled"].findings == []


def test_an_invalid_escape_is_a_warning_not_a_syntax_error(tmp_path, no_linters):
    _bundle(tmp_path, {"tool.py": b"import re\nre.compile('\\d+')\n"})

    assert _reviews_by_name(tmp_path)["bundled"].findings == []


def test_a_file_over_the_size_cap_is_not_read(tmp_path, monkeypatch):
    monkeypatch.setattr(script_checks, "MAX_BYTES", 8)
    skill = _bundle(tmp_path, {"big.py": b"def f(:\n    pass\n"})

    assert script_findings(skill, which=_tools(), runner=process.FakeRunner()) == []


def test_a_script_reached_through_a_link_out_of_the_skill_is_not_read(tmp_path, no_linters):
    outside = tmp_path / "outside.sh"
    outside.write_bytes(b"echo a\r\n")
    skill = _bundle(tmp_path, {})
    try:
        os.symlink(outside, skill / "linked.sh")
    except (OSError, NotImplementedError):
        pytest.skip("this platform cannot make symlinks")

    assert script_findings(skill, which=_tools(), runner=process.FakeRunner()) == []


@needs_bash
def test_a_clean_bundle_has_no_script_findings(tmp_path, no_linters):
    _bundle(tmp_path, {"run.sh": b"#!/bin/sh\necho hi\n", "tool.py": b"print('hi')\n"})

    review = _reviews_by_name(tmp_path)["bundled"]
    assert review.state == "ok"
    assert review.findings == []


def test_a_script_with_another_shebang_is_not_checked_as_bash(tmp_path):
    # a valid fish script: bash -n would reject it, so no bash is spawned for it
    skill = _bundle(tmp_path, {"run.sh": b"#!/usr/bin/env fish\nif test -f x.txt\n  echo y\nend\n"})
    runner = process.FakeRunner(lambda call: process.reply(stderr="bash: syntax error\n", returncode=2))

    assert script_findings(skill, which=_tools("bash"), runner=runner) == []
    assert runner.calls == []


def test_a_script_with_an_env_bash_shebang_is_checked_as_bash(tmp_path):
    skill = _bundle(tmp_path, {"run.sh": b"#!/usr/bin/env bash\nif true; then\n"})
    runner = process.FakeRunner(lambda call: process.reply(stderr="bash: line 2: syntax error\n", returncode=2))

    findings = script_findings(skill, which=_tools("bash"), runner=runner)

    assert [code for _, code, _ in findings] == ["script-syntax"]


def test_a_skill_at_the_checkout_root_has_no_script_bundle(tmp_path, no_linters):
    _skill(tmp_path, ".", name="root", description="Use for root work.")
    (tmp_path / "tests" / "fixtures").mkdir(parents=True)
    (tmp_path / "tests" / "fixtures" / "broken.py").write_bytes(b"def f(:\n")

    review = _reviews_by_name(tmp_path)["root"]
    assert [code for code in _codes(review) if code.startswith("script-")] == []


def test_vendored_and_scaffold_trees_are_not_the_skills_scripts(tmp_path, no_linters):
    _bundle(tmp_path, {"site-packages/bad.py": b"def f(:\n", "templates/bad.py": b"def f(:\n"})

    assert _reviews_by_name(tmp_path)["bundled"].state == "ok"


def test_a_tool_that_cannot_start_adds_no_finding(tmp_path):
    skill = _bundle(tmp_path, {"tool.py": b"x = 1\n", "run.sh": b"echo hi\n"})

    def cannot_start(call):
        raise FileNotFoundError(2, "No such file or directory", call.argv[0])

    findings = script_findings(skill, which=_tools("bash", "ruff", "shellcheck"),
                               runner=process.FakeRunner(cannot_start))

    assert findings == []


def test_a_script_in_a_dot_directory_is_checked(tmp_path, no_linters):
    _bundle(tmp_path, {".scripts/run.sh": b"#!/bin/sh\r\necho hi\r\n"})

    assert _codes(_reviews_by_name(tmp_path)["bundled"]) == ["script-crlf"]


def test_a_python_syntax_error_is_not_counted_again_by_ruff(tmp_path):
    skill = _bundle(tmp_path, {"tool.py": b"def f(:\n"})
    runner = _linter_runner(ruff=[{"code": "invalid-syntax"}])

    findings = script_findings(skill, which=_tools("ruff"), runner=runner)

    assert [code for _, code, _ in findings] == ["script-syntax"]
