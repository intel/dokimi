# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The dependency-advisory ignore lists must name the same IDs and point at each other."""

from __future__ import annotations

import re
import shutil
import subprocess
import tomllib
from pathlib import Path

import pytest

PROJECT = Path(__file__).resolve().parents[1]
REPO = PROJECT.parent
OSV_CONFIG = PROJECT / "osv-scanner.toml"
CI_WORKFLOW = REPO / ".github" / "workflows" / "pip-audit.yml"
SECURITY = PROJECT / "SECURITY.md"
ADVISORY_ID = r"PYSEC-\d{4}-\d+|GHSA(?:-[0-9a-z]{4}){3}"


def _osv_ids() -> set[str]:
    data = tomllib.loads(OSV_CONFIG.read_text(encoding="utf-8"))
    return {entry["id"] for entry in data["IgnoredVulns"]}


def _pip_audit_ids() -> set[str]:
    return set(
        re.findall(
            rf"--ignore-vuln\s+({ADVISORY_ID})",
            CI_WORKFLOW.read_text(encoding="utf-8"),
        )
    )


def test_osv_and_pip_audit_ignore_the_same_ids():
    assert _osv_ids() == _pip_audit_ids()


def test_ci_and_security_name_the_osv_config():
    """A removed advisory must be dropped from all three lists, so each names the file."""
    assert "dokimi/osv-scanner.toml" in CI_WORKFLOW.read_text(encoding="utf-8")
    assert "osv-scanner.toml" in SECURITY.read_text(encoding="utf-8")


def _names_id(text: str, advisory: str) -> bool:
    """Whole-ID match, so PYSEC-2026-24470 does not document PYSEC-2026-2447."""
    return re.search(rf"(?<![\w-]){re.escape(advisory)}(?![\w-])", text) is not None


def test_id_match_respects_boundaries():
    assert _names_id("see `PYSEC-2026-2447`.", "PYSEC-2026-2447")
    assert not _names_id("see PYSEC-2026-24470", "PYSEC-2026-2447")
    assert not _names_id("see XPYSEC-2026-2447", "PYSEC-2026-2447")


def test_security_documents_every_ignored_id():
    security = SECURITY.read_text(encoding="utf-8")
    undocumented = sorted(i for i in _osv_ids() if not _names_id(security, i))
    assert not undocumented, (
        f"SECURITY.md does not explain these ignored advisories: {undocumented}"
    )


def _lock_advisory_list() -> str:
    """The list after "pip-audit against the locks ... reports these advisories": the locks' ones."""
    _, found, after = SECURITY.read_text(encoding="utf-8").partition(
        "fix the locks can take:\n\n"
    )
    assert found, "SECURITY.md no longer introduces the list of advisories in the locks"
    return after.split("\n\n", 1)[0]


def test_security_lists_no_advisory_the_lists_stopped_ignoring():
    """SECURITY.md says a removed advisory leaves that list with the other two."""
    listed = set(re.findall(ADVISORY_ID, _lock_advisory_list()))
    stale = sorted(listed - _osv_ids())
    assert not stale, f"SECURITY.md lists advisories no list ignores: {stale}"


def test_hypothesis_example_database_is_git_ignored():
    git = shutil.which("git")
    if git is None or not (REPO / ".git").exists():
        pytest.skip("needs a git checkout to ask git check-ignore")
    result = subprocess.run(
        [git, "check-ignore", "-q", ".hypothesis/examples/probe"],
        cwd=REPO,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, ".hypothesis/ is not ignored by the root .gitignore"
