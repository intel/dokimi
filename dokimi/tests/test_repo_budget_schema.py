# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""`repo budget --json` is a `repo_budget.v1` artifact: stamped, and held to its schema."""

from __future__ import annotations

import copy
import json

import pytest

from dokimi.artifacts import ArtifactValidationError, validate_artifact
from dokimi.cli import build_parser

SKILL = "---\nname: {name}\ndescription: {description}\n---\nDo the thing.\n"
DESCRIPTION = "Reviews pull requests for security issues in authentication and session handling code"


def _populated_tree(root):
    """Guidance, an import, a rule, two near-duplicate skills, a hidden skill, an MCP server and a warning."""
    files = {
        "CLAUDE.md": "See @docs/style.md for the style.\n",
        "AGENTS.md": "Agents read this.\n",  # turned off by CLAUDE.md: a warning
        "docs/style.md": "Use four spaces.\n",
        "sub/CLAUDE.md": "Nested guidance.\n",
        ".claude/rules/testing.md": "Run the tests.\n",
        ".claude/skills/review/SKILL.md": SKILL.format(name="review", description=DESCRIPTION),
        ".claude/skills/audit/SKILL.md": SKILL.format(name="audit", description=DESCRIPTION),
        ".claude/skills/hidden/SKILL.md": "---\nname: hidden\ndisable-model-invocation: true\n---\nBody.\n",
        ".mcp.json": json.dumps({"mcpServers": {"docs": {}, "db": {}}}),
    }
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")


def _budget_json(root, capsys, *extra):
    args = build_parser().parse_args(["repo", "budget", str(root), "--json", *extra])
    args.func(args)
    return json.loads(capsys.readouterr().out)


@pytest.mark.outcome("cjm-repo-budget")
def test_json_is_a_stamped_repo_budget_artifact(tmp_path, capsys):
    _populated_tree(tmp_path)
    data = _budget_json(tmp_path, capsys)
    assert data["schema_version"] == "repo_budget.v1"
    validate_artifact("repo_budget", data)
    # every field is populated, so the schema was checked against more than empty lists
    assert {e["kind"] for e in data["entries"]} == {"guidance", "import", "rule", "skill listing", "skill body"}
    assert {e["when"] for e in data["entries"]} == {"start", "lazy", "on demand", "off"}
    assert data["skills"] == 3
    assert [(d["a"], d["b"]) for d in data["duplicate_skills"]] == [("audit", "review")]
    assert data["mcp_servers"] == ["db", "docs"]
    assert data["warnings"]


def test_out_file_is_the_same_artifact(tmp_path, capsys):
    _populated_tree(tmp_path)
    dest = tmp_path / "out" / "budget.json"  # save_artifact creates the directory
    printed = _budget_json(tmp_path, capsys, "--out", str(dest))
    assert json.loads(dest.read_text(encoding="utf-8")) == printed


def test_nothing_to_budget_is_still_valid(tmp_path, capsys):
    data = _budget_json(tmp_path, capsys)
    assert data["entries"] == [] and data["totals"] == {"start": 0, "lazy": 0, "on demand": 0, "off": 0}
    validate_artifact("repo_budget", data)


@pytest.mark.parametrize("field", ["schema_version", "tree", "entries", "totals", "skills", "duplicate_skills",
                                   "mcp_servers", "warnings"])
@pytest.mark.outcome("cjm-repo-budget")
def test_a_payload_missing_a_field_is_rejected(tmp_path, capsys, field):
    _populated_tree(tmp_path)
    data = _budget_json(tmp_path, capsys)
    del data[field]
    with pytest.raises(ArtifactValidationError, match=field):
        validate_artifact("repo_budget", data)


@pytest.mark.parametrize("change, message", [
    (lambda d: d["entries"][0].pop("chars"), "chars"),
    (lambda d: d["entries"][0].update(when="eventually"), "eventually"),
    (lambda d: d["totals"].pop("off"), "off"),
    (lambda d: d["duplicate_skills"][0].update(overlap=1.5), "1.5"),
    (lambda d: d.update(unexpected=1), "unexpected"),
    # closed at every level, not only the top: a key the code never writes is a changed shape
    (lambda d: d["entries"][0].update(tokens=1), "tokens"),
    (lambda d: d["totals"].update(later=0), "later"),
    (lambda d: d["duplicate_skills"][0].update(score=1), "score"),
])
def test_a_malformed_payload_is_rejected(tmp_path, capsys, change, message):
    _populated_tree(tmp_path)
    data = copy.deepcopy(_budget_json(tmp_path, capsys))
    change(data)
    with pytest.raises(ArtifactValidationError, match=message):
        validate_artifact("repo_budget", data)
