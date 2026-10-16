# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The contract check must fail when the doc a promise points at is gone."""

from __future__ import annotations

import json

import pytest

from tests.outcomes import plugin


def _contract(tmp_path, monkeypatch, doc_text, ref):
    monkeypatch.setattr(plugin, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(plugin, "_results", {"ev": ["passed"]})
    (tmp_path / "doc.md").write_text(doc_text, encoding="utf-8")
    path = tmp_path / "c.json"
    path.write_text(json.dumps({"id": "C", "intent_refs": [ref], "evidence_ids": ["ev"]}), encoding="utf-8")
    return plugin._check(path)


def test_anchor_that_matches_a_heading_passes(tmp_path, monkeypatch):
    result = _contract(tmp_path, monkeypatch, "## 4. Reuse same set\n", "doc.md#4-reuse-same-set")
    assert result["verdict"] == "pass"


def test_renamed_heading_is_an_error(tmp_path, monkeypatch):
    result = _contract(tmp_path, monkeypatch, "## 4. Something else\n", "doc.md#4-reuse-same-set")
    assert result["verdict"] == "error"
    assert result["missing_refs"] == ["doc.md#4-reuse-same-set"]


def test_comment_inside_a_code_fence_is_not_a_heading(tmp_path, monkeypatch):
    doc = "## Real\n\n```bash\n# not-a-heading\n```\n"
    assert _contract(tmp_path, monkeypatch, doc, "doc.md#not-a-heading")["verdict"] == "error"


def test_repeated_heading_gets_a_numbered_slug(tmp_path, monkeypatch):
    assert _contract(tmp_path, monkeypatch, "# A\n# A\n", "doc.md#a-1")["verdict"] == "pass"


def test_verdict_pass_when_all_claimed_tests_pass(tmp_path, monkeypatch):
    monkeypatch.setattr(plugin, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(plugin, "_results", {"ev1": ["passed", "passed"]})
    (tmp_path / "doc.md").write_text("## Test", encoding="utf-8")
    path = tmp_path / "c.json"
    path.write_text(json.dumps({"id": "C", "intent_refs": ["doc.md#test"], "evidence_ids": ["ev1"]}), encoding="utf-8")
    result = plugin._check(path)
    assert result["verdict"] == "pass"
    assert result["evidence"][0]["verdict"] == "pass"


def test_verdict_fail_when_any_claimed_test_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(plugin, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(plugin, "_results", {"ev1": ["passed", "failed"]})
    (tmp_path / "doc.md").write_text("## Test", encoding="utf-8")
    path = tmp_path / "c.json"
    path.write_text(json.dumps({"id": "C", "intent_refs": ["doc.md#test"], "evidence_ids": ["ev1"]}), encoding="utf-8")
    result = plugin._check(path)
    assert result["verdict"] == "fail"
    assert result["evidence"][0]["verdict"] == "fail"


def test_verdict_inconclusive_when_all_claimed_tests_skipped(tmp_path, monkeypatch):
    monkeypatch.setattr(plugin, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(plugin, "_results", {"ev1": ["skipped", "skipped"]})
    (tmp_path / "doc.md").write_text("## Test", encoding="utf-8")
    path = tmp_path / "c.json"
    path.write_text(json.dumps({"id": "C", "intent_refs": ["doc.md#test"], "evidence_ids": ["ev1"]}), encoding="utf-8")
    result = plugin._check(path)
    assert result["verdict"] == "inconclusive"
    assert result["evidence"][0]["verdict"] == "inconclusive"


def test_verdict_pass_when_some_tests_pass_and_some_skipped(tmp_path, monkeypatch):
    """When a parametrized test has both passing and skipped cases, the verdict is pass."""
    monkeypatch.setattr(plugin, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(plugin, "_results", {"ev1": ["passed", "skipped"]})
    (tmp_path / "doc.md").write_text("## Test", encoding="utf-8")
    path = tmp_path / "c.json"
    path.write_text(json.dumps({"id": "C", "intent_refs": ["doc.md#test"], "evidence_ids": ["ev1"]}), encoding="utf-8")
    result = plugin._check(path)
    assert result["verdict"] == "pass"
    assert result["evidence"][0]["verdict"] == "pass"


def test_verdict_error_when_no_test_claims_id(tmp_path, monkeypatch):
    monkeypatch.setattr(plugin, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(plugin, "_results", {})
    (tmp_path / "doc.md").write_text("## Test", encoding="utf-8")
    path = tmp_path / "c.json"
    path.write_text(json.dumps({"id": "C", "intent_refs": ["doc.md#test"], "evidence_ids": ["ev1"]}), encoding="utf-8")
    result = plugin._check(path)
    assert result["verdict"] == "error"
    assert result["evidence"][0]["verdict"] == "error"


@pytest.mark.outcome("outcome-contracts")
def test_plugin_handles_no_terminal_reporter(tmp_path, monkeypatch):
    """Outcome plugin does not crash when terminalreporter is not available (e.g., -p no:terminal)."""
    from pathlib import Path

    # Create a minimal session mock with no terminalreporter plugin
    class MockConfig:
        def getoption(self, key):
            if key == "outcomes":
                return True
            elif key == "outcomes_report":
                return str(tmp_path / "outcomes-result.json")

        class PluginManager:
            def get_plugin(self, name):
                return None

        pluginmanager = PluginManager()

    class MockSession:
        config = MockConfig()
        exitstatus = 0

    # Write a valid contract in a fake contract dir and set up results
    contract_dir = tmp_path / "contracts"
    contract_dir.mkdir()
    monkeypatch.setattr(plugin, "CONTRACT_DIR", contract_dir)
    monkeypatch.setattr(plugin, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(plugin, "_results", {"ev1": ["passed"]})
    (tmp_path / "doc.md").write_text("## Test", encoding="utf-8")
    (contract_dir / "c.json").write_text(json.dumps({"id": "C", "intent_refs": ["doc.md#test"], "evidence_ids": ["ev1"]}), encoding="utf-8")

    session = MockSession()
    # This should not raise AttributeError
    plugin.pytest_sessionfinish(session, 0)

    # Verify report was written and exit status was set
    assert (tmp_path / "outcomes-result.json").exists()
    assert session.exitstatus == 0
