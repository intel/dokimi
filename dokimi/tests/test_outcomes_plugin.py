# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The contract check must fail when the doc a promise points at is gone."""

from __future__ import annotations

import json

import pytest

from tests.outcomes import plugin

pytest_plugins = ["pytester"]


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


def test_plugin_handles_no_terminal_reporter(tmp_path, monkeypatch):
    """Outcome plugin does not crash when terminalreporter is not available (e.g., -p no:terminal)."""
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
    monkeypatch.setattr(plugin, "REGISTRY", None)
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


@pytest.mark.parametrize(
    "body, verdict, ret",
    [("assert True", "pass", 0), ("assert False", "fail", 1), ("pytest.skip('x')", "inconclusive", 3)],
)
def test_inner_session_exit_code(pytester, monkeypatch, body, verdict, ret):
    contracts = pytester.path / "contracts"
    contracts.mkdir()
    (pytester.path / "doc.md").write_text("## Test\n", encoding="utf-8")
    (contracts / "c.json").write_text(json.dumps(
        {"id": "C", "intent_refs": ["doc.md#test"], "evidence_ids": ["ev"]}), encoding="utf-8")
    monkeypatch.setattr(plugin, "CONTRACT_DIR", contracts)
    monkeypatch.setattr(plugin, "REGISTRY", None)
    monkeypatch.setattr(plugin, "REPO_ROOT", pytester.path)
    monkeypatch.setattr(plugin, "_results", {})
    pytester.makepyfile(f"import pytest\n\n@pytest.mark.outcome('ev')\ndef test_a():\n    {body}\n")
    result = pytester.runpytest("-p", "tests.outcomes.plugin", "--outcomes", "--outcomes-report=r.json")
    assert result.ret == ret
    assert json.loads((pytester.path / "r.json").read_text())["verdict"] == verdict


def test_inner_session_unclaimed_id_is_error(pytester, monkeypatch):
    contracts = pytester.path / "contracts"
    contracts.mkdir()
    (pytester.path / "doc.md").write_text("## Test\n", encoding="utf-8")
    (contracts / "c.json").write_text(json.dumps(
        {"id": "C", "intent_refs": ["doc.md#test"], "evidence_ids": ["ev"]}), encoding="utf-8")
    monkeypatch.setattr(plugin, "CONTRACT_DIR", contracts)
    monkeypatch.setattr(plugin, "REGISTRY", None)
    monkeypatch.setattr(plugin, "REPO_ROOT", pytester.path)
    monkeypatch.setattr(plugin, "_results", {})
    pytester.makepyfile("def test_a():\n    assert True\n")
    result = pytester.runpytest("-p", "tests.outcomes.plugin", "--outcomes", "--outcomes-report=r.json")
    assert result.ret == 2
    assert json.loads((pytester.path / "r.json").read_text())["verdict"] == "error"


def test_contract_with_mixed_evidence_ids(pytester, monkeypatch):
    """Test contract with one passing and one unclaimed evidence id -> rc 2, error."""
    contracts = pytester.path / "contracts"
    contracts.mkdir()
    (pytester.path / "doc.md").write_text("## Test\n", encoding="utf-8")
    (contracts / "c.json").write_text(json.dumps({
        "id": "C", "intent_refs": ["doc.md#test"],
        "evidence_ids": ["ev_a", "ev_b"]  # ev_a claimed+passing, ev_b unclaimed
    }), encoding="utf-8")
    monkeypatch.setattr(plugin, "CONTRACT_DIR", contracts)
    monkeypatch.setattr(plugin, "REGISTRY", None)
    monkeypatch.setattr(plugin, "REPO_ROOT", pytester.path)
    monkeypatch.setattr(plugin, "_results", {})
    pytester.makepyfile("import pytest\n\n@pytest.mark.outcome('ev_a')\ndef test_a():\n    assert True\n")
    result = pytester.runpytest("-p", "tests.outcomes.plugin", "--outcomes", "--outcomes-report=r.json")
    assert result.ret == 2
    assert json.loads((pytester.path / "r.json").read_text())["verdict"] == "error"


def test_passing_claimed_test_with_unrelated_failing_test(pytester, monkeypatch):
    """Test claimed test passes, but unrelated test fails -> rc 1, verdict pass."""
    contracts = pytester.path / "contracts"
    contracts.mkdir()
    (pytester.path / "doc.md").write_text("## Test\n", encoding="utf-8")
    (contracts / "c.json").write_text(json.dumps({
        "id": "C", "intent_refs": ["doc.md#test"],
        "evidence_ids": ["ev"]
    }), encoding="utf-8")
    monkeypatch.setattr(plugin, "CONTRACT_DIR", contracts)
    monkeypatch.setattr(plugin, "REGISTRY", None)
    monkeypatch.setattr(plugin, "REPO_ROOT", pytester.path)
    monkeypatch.setattr(plugin, "_results", {})
    pytester.makepyfile(
        "import pytest\n\n"
        "@pytest.mark.outcome('ev')\n"
        "def test_claimed():\n"
        "    assert True\n\n"
        "def test_unrelated():\n"
        "    assert False\n"
    )
    result = pytester.runpytest("-p", "tests.outcomes.plugin", "--outcomes", "--outcomes-report=r.json")
    assert result.ret == 1
    assert json.loads((pytester.path / "r.json").read_text())["verdict"] == "pass"


def test_contract_with_missing_implementation_ref(pytester, monkeypatch):
    """Test contract with missing implementation_refs file is detected."""
    contracts = pytester.path / "contracts"
    contracts.mkdir()
    (pytester.path / "doc.md").write_text("## Test\n", encoding="utf-8")
    (contracts / "c.json").write_text(json.dumps({
        "id": "C", "intent_refs": ["doc.md#test"],
        "evidence_ids": ["ev"],
        "implementation_refs": ["missing_file.py"]  # This file doesn't exist
    }), encoding="utf-8")
    monkeypatch.setattr(plugin, "CONTRACT_DIR", contracts)
    monkeypatch.setattr(plugin, "REGISTRY", None)
    monkeypatch.setattr(plugin, "REPO_ROOT", pytester.path)
    monkeypatch.setattr(plugin, "_results", {})
    pytester.makepyfile("import pytest\n\n@pytest.mark.outcome('ev')\ndef test_a():\n    assert True\n")
    result = pytester.runpytest("-p", "tests.outcomes.plugin", "--outcomes", "--outcomes-report=r.json")
    assert result.ret == 2
    assert json.loads((pytester.path / "r.json").read_text())["verdict"] == "error"


def _inner_session(pytester, monkeypatch, contracts, test_source):
    """Run ``test_source`` under ``--outcomes`` against ``contracts`` (a list of contract dicts)."""
    contract_dir = pytester.path / "contracts"
    contract_dir.mkdir()
    (pytester.path / "doc.md").write_text("## Test\n", encoding="utf-8")
    for n, contract in enumerate(contracts):
        (contract_dir / f"c{n}.json").write_text(json.dumps(contract), encoding="utf-8")
    monkeypatch.setattr(plugin, "CONTRACT_DIR", contract_dir)
    monkeypatch.setattr(plugin, "REGISTRY", None)
    monkeypatch.setattr(plugin, "REPO_ROOT", pytester.path)
    monkeypatch.setattr(plugin, "_results", {})
    pytester.makepyfile(test_source)
    result = pytester.runpytest("-p", "tests.outcomes.plugin", "--outcomes", "--outcomes-report=r.json")
    return result.ret, json.loads((pytester.path / "r.json").read_text())


def test_a_claimed_test_whose_setup_fails_fails_its_contract(pytester, monkeypatch):
    ret, report = _inner_session(
        pytester, monkeypatch, [{"id": "C", "intent_refs": ["doc.md#test"], "evidence_ids": ["ev"]}],
        "import pytest\n\n"
        "@pytest.fixture\n"
        "def broken():\n"
        "    raise RuntimeError('setup')\n\n"
        "@pytest.mark.outcome('ev')\n"
        "def test_a(broken):\n"
        "    assert True\n",
    )
    assert ret == 1
    assert report["verdict"] == "fail"
    assert report["outcomes"][0]["evidence"][0]["verdict"] == "fail"


def test_a_contract_with_no_evidence_ids_is_an_error(pytester, monkeypatch):
    ret, report = _inner_session(
        pytester, monkeypatch, [{"id": "C", "intent_refs": ["doc.md#test"], "evidence_ids": []}],
        "def test_a():\n    assert True\n",
    )
    assert ret == 2
    assert report["verdict"] == "error"


def test_an_empty_contract_directory_is_an_error(pytester, monkeypatch):
    ret, report = _inner_session(pytester, monkeypatch, [], "def test_a():\n    assert True\n")
    assert ret == 2
    assert report == {"verdict": "error", "outcomes": []}
