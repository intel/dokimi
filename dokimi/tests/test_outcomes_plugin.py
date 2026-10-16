# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The contract check must fail when the doc a promise points at is gone."""

from __future__ import annotations

import json

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
