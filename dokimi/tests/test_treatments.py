# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Tests for the treatment-arm abstraction and factory."""

import pytest
from tests.conftest import DEMO_SKILL_MARKER

from dokimi.utils import parse_frontmatter
from dokimi.treatments import (
    AgentConfig,
    BaselineTreatment,
    DocTreatment,
    AgentProfileTreatment,
    SkillTreatment,
    create_treatment,
    create_treatments,
)
from dokimi.agent_profiles import load_agent_profile, AgentProfile
from dokimi.skills import load_skill, Skill
from dokimi.plugins import (
    create_plugin,
    create_plugins,
    plugin_set_metadata,
    validate_plugins_for_harness,
    wrap_treatments,
)
from dokimi.eval.arms_run import resolve_doc_library_id


# ── frontmatter ─────────────────────────────────────────────────────────
def test_parse_frontmatter_basic():
    meta, body = parse_frontmatter("---\nname: x\ndescription: y\n---\nhello\nworld")
    assert meta == {"name": "x", "description": "y"}
    assert body == "hello\nworld"


def test_parse_frontmatter_none():
    meta, body = parse_frontmatter("no frontmatter here")
    assert meta == {}
    assert body == "no frontmatter here"


def test_parse_frontmatter_unterminated():
    text = "---\nname: x\nbut never closed"
    meta, body = parse_frontmatter(text)
    assert meta == {}
    assert body == text


def test_parse_frontmatter_strips_bom_when_no_frontmatter():
    meta, body = parse_frontmatter("﻿hello")
    assert meta == {}
    assert body == "hello"  # BOM removed


def test_parse_frontmatter_opening_fence_must_be_exactly_three_dashes():
    # A thematic break or a "--- title" line opens nothing: the closing-fence rule
    # is "exactly ---", and the opening line gets the same rule.
    for first in ("-----", "--- not a fence", "---x", "  ---"):
        text = f"{first}\nintro paragraph\n---\nrest"
        meta, body = parse_frontmatter(text)
        assert meta == {}
        assert body == text


def test_parse_frontmatter_unterminated_strips_bom():
    meta, body = parse_frontmatter("\ufeff---\nname: x\nnever closed")
    assert meta == {}
    assert body == "---\nname: x\nnever closed"


# ── baseline arm ────────────────────────────────────────────────────────
def test_baseline_treatment():
    t = BaselineTreatment()
    cfg = t.prepare("q", "oneTBB")
    assert isinstance(cfg, AgentConfig)
    assert cfg.system_prompt is None
    assert cfg.injected_context == []
    assert not cfg.has_context


# ── agent profile arm ───────────────────────────────────────────────────
def test_agent_profile_loads_and_prepares():
    profile = load_agent_profile("data/agent_profiles/concise_expert.md")
    assert isinstance(profile, AgentProfile)
    assert profile.id == "concise_expert"
    assert "senior" in profile.system_prompt.lower()

    t = AgentProfileTreatment(profile)
    assert t.name == "profile:concise_expert"
    cfg = t.prepare("q", "oneTBB")
    assert cfg.system_prompt == profile.system_prompt
    assert cfg.metadata["arm_kind"] == "agent_profile"


def test_agent_profile_empty_body_rejected(tmp_path):
    p = tmp_path / "empty.md"
    p.write_text("---\nid: e\n---\n\n")
    with pytest.raises(ValueError):
        load_agent_profile(p)


def test_agent_profile_blank_id_rejected(tmp_path):
    p = tmp_path / "blank.md"
    p.write_text('---\nid: "   "\n---\nbody here')
    with pytest.raises(ValueError):
        load_agent_profile(p)


# ── skill arm ───────────────────────────────────────────────────────────
def test_skill_loads_and_prepares(demo_skill):
    skill = load_skill(demo_skill)
    assert isinstance(skill, Skill)
    assert skill.name == "demo-skill"
    assert skill.description

    t = SkillTreatment(skill)
    assert t.name == "skill:demo-skill"
    cfg = t.prepare("How do I parallelize a loop?", "demo")
    assert cfg.has_context
    assert len(cfg.injected_context) == 1
    chunk = cfg.injected_context[0]
    assert DEMO_SKILL_MARKER in chunk["content"]
    assert chunk["source"] == "skill:demo-skill"
    assert cfg.metadata["skill_name"] == "demo-skill"


def test_skill_missing_description_rejected(tmp_path):
    d = tmp_path / "bad-skill"
    d.mkdir()
    (d / "SKILL.md").write_text("---\nname: bad\n---\nbody only")
    with pytest.raises(ValueError):
        load_skill(d)


def test_skill_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_skill(tmp_path / "does-not-exist")


def test_skill_rejects_non_skill_md_file(tmp_path):
    p = tmp_path / "notes.md"
    p.write_text("---\nname: x\ndescription: y\n---\nbody")
    with pytest.raises(ValueError):
        load_skill(p)


def test_skill_blank_name_rejected(tmp_path):
    d = tmp_path / "blank-name"
    d.mkdir()
    (d / "SKILL.md").write_text('---\nname: "  "\ndescription: y\n---\nbody')
    with pytest.raises(ValueError):
        load_skill(d)


# ── doc arm (with a fake client) ────────────────────────────────────────
class _FakeClient:
    def resolve_library_id(self, name):
        return f"fake/{name}"

    def get_library_docs(self, library_id, query, max_results=5, **kw):
        return [
            {"content": "parallel_for runs a loop in parallel", "relevance_score": 0.9},
            {"content": "totally unrelated text about cooking", "relevance_score": 0.1},
        ]

    def check_connection(self):
        return True


def test_doc_treatment_retrieves_and_injects():
    t = DocTreatment(_FakeClient(), rerank_threshold=0.0, keep=2)
    cfg = t.prepare("how to use parallel_for", "oneTBB", "fake/oneTBB")
    assert cfg.has_context
    assert cfg.metadata["arm_kind"] == "docs"
    assert cfg.metadata["raw_count"] == 2


def test_doc_treatment_retrieval_error_is_swallowed():
    class _Boom(_FakeClient):
        def get_library_docs(self, *a, **k):
            raise RuntimeError("network down")

    t = DocTreatment(_Boom(), rerank_threshold=0.0)
    cfg = t.prepare("q", "oneTBB", "id")
    assert not cfg.has_context
    assert cfg.metadata["error"] == "retrieval_failed"


def test_doc_library_id_resolves_before_or_after_plugin_wrapping(tmp_path):
    from dokimi.registry import ProductRegistry

    registry_path = tmp_path / "products.yaml"
    registry_path.write_text(
        "products:\n"
        "  demo:\n"
        "    name: demo\n"
        "    description: x\n"
        "    context7_id: declared/id\n",
        encoding="utf-8",
    )
    registry = ProductRegistry(path=registry_path)

    t = DocTreatment(_FakeClient(), rerank_threshold=0.0)
    wrapped = wrap_treatments([t], [create_plugin("plugin:caveman")])

    # Unregistered product: the doc client resolves it, wrapped or not.
    assert resolve_doc_library_id([t], "notaproduct", registry=registry) == "fake/notaproduct"
    assert resolve_doc_library_id(wrapped, "notaproduct", registry=registry) == "fake/notaproduct"
    # Explicit flag wins over everything.
    assert (
        resolve_doc_library_id(
            wrapped, "notaproduct", explicit_id="manual/id", registry=registry
        )
        == "manual/id"
    )
    # A registered product's declared context7_id wins over client resolution.
    assert resolve_doc_library_id(wrapped, "demo", registry=registry) == "declared/id"
    # Default registry (the committed products.yaml) is used when none is given.
    assert resolve_doc_library_id([t], "notaproduct") == "fake/notaproduct"


# ── factory ─────────────────────────────────────────────────────────────
def test_create_treatment_baseline():
    assert isinstance(create_treatment("baseline"), BaselineTreatment)


def test_create_treatment_profile_and_skill(demo_skill):
    assert isinstance(
        create_treatment("profile:data/agent_profiles/concise_expert.md"), AgentProfileTreatment
    )
    assert isinstance(
        create_treatment(f"skill:{demo_skill.as_posix()}"), SkillTreatment
    )


def test_create_treatment_unknown_spec():
    with pytest.raises(ValueError):
        create_treatment("nonsense:foo")


def test_create_treatment_missing_arg():
    with pytest.raises(ValueError):
        create_treatment("profile:")


def test_create_treatments_rejects_duplicate_names():
    with pytest.raises(ValueError):
        create_treatments(["baseline", "baseline"])


def test_create_treatment_empty_mcp_ref():
    with pytest.raises(ValueError):
        create_treatment("mcp:")


# ── plugins ─────────────────────────────────────────────────────────────
def test_caveman_plugin_wraps_treatment_system_prompt():
    plugin = create_plugin("plugin:caveman:ultra")
    wrapped = wrap_treatments([BaselineTreatment()], [plugin])[0]

    cfg = wrapped.prepare("q", "oneTBB")

    assert "ultra-compressed caveman" in cfg.system_prompt
    assert cfg.metadata["plugin_set"] == "caveman:ultra"
    assert cfg.metadata["plugins"][0]["id"] == "caveman"
    assert cfg.metadata["plugins"][0]["config"]["level"] == "ultra"


def test_plugin_set_metadata_empty_and_caveman():
    assert plugin_set_metadata([])["plugin_set"] == "none"
    meta = plugin_set_metadata(create_plugins(["plugin:caveman"]))
    assert meta["plugin_set"] == "caveman:full"
    assert meta["plugin_set_id"].startswith("sha256:")


def test_prompt_plugins_reject_terminal_bench_harness():
    plugins = create_plugins(["plugin:caveman"])

    with pytest.raises(ValueError, match="not supported by harness 'terminal-bench:terminus'"):
        validate_plugins_for_harness(plugins, "terminal-bench:terminus")


def test_prompt_plugins_accept_agent_harness():
    validate_plugins_for_harness(create_plugins(["plugin:caveman"]), "agent")


def test_create_plugin_rejects_unknown():
    with pytest.raises(ValueError):
        create_plugin("plugin:unknown")
