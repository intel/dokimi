# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Arm-spec grammar: the interface of :mod:`dokimi.treatments.specs` is the test surface."""

from __future__ import annotations

import pytest

from dokimi.treatments import create_treatment
from dokimi.treatments.specs import (
    VALID_SPECS,
    ArmSpecError,
    combine_specs,
    content_refs,
    parse_arm_spec,
    parse_part,
    split_arm_list,
    split_combined,
    split_combined_name,
)


@pytest.mark.parametrize("spec, parts", [
    ("baseline", ["baseline"]),
    ("docs+skill:data/skills/test", ["docs", "skill:data/skills/test"]),
    ("docs + skill:x", ["docs", "skill:x"]),
    ("docs+agent", ["docs", "agent"]),
    ("skill:a+skill-agent:b", ["skill:a", "skill-agent:b"]),
    ("docs+skill-catalog:/s", ["docs", "skill-catalog:/s"]),
    ("docs+profile:p/expert.md+skill:s", ["docs", "profile:p/expert.md", "skill:s"]),
    # '+' inside a path is not a separator ...
    ("docs+skill:data/my+folder/skill", ["docs", "skill:data/my+folder/skill"]),
    ("docs:local:/d/old+b.md", ["docs:local:/d/old+b.md"]),
    # ... including a path segment that merely starts with a kind's letters.
    ("skill:/x/c+docs-guide", ["skill:/x/c+docs-guide"]),
    ("skill:/x/a+baselines", ["skill:/x/a+baselines"]),
    # A spec that starts with mcp: is never split (query strings carry '+').
    ("mcp:http=https://x/mcp?q=a+b", ["mcp:http=https://x/mcp?q=a+b"]),
    ("mcp:http=x+skill:y", ["mcp:http=x+skill:y"]),
    ("docs+mcp:http=x?q=a+b", ["docs", "mcp:http=x?q=a+b"]),
])
def test_split_combined(spec, parts):
    assert split_combined(spec) == parts


@pytest.mark.parametrize("part, kind, arg, doc_source, content_ref, name", [
    ("baseline", "baseline", "", "", None, "baseline"),
    ("docs", "docs", "", "context7", None, "docs"),
    ("docs:", "docs", "", "context7", None, "docs"),
    ("docs:url:https://x", "docs", "url:https://x", "url:https://x", None, "docs"),
    ("docs:local:./d", "docs", "local:./d", "local:./d", "local:./d", "docs"),
    ("agent", "agent", "", "context7", None, "agent"),
    ("agent:local:./d", "agent", "local:./d", "local:./d", "local:./d", "agent"),
    ("agent:mcp:http=x", "agent", "mcp:http=x", "mcp:http=x", None, "agent"),
    ("mcp:http=x", "mcp", "http=x", "mcp:http=x", None, "mcp_doc"),
    ("skill:./s", "skill", "./s", None, "./s", "skill:LABEL"),
    ("skill-agent:./s", "skill-agent", "./s", None, "./s", "skill_agent:LABEL"),
    ("profile:./p.md", "profile", "./p.md", None, "./p.md", "profile:LABEL"),
    # Named after its argument, not its content: the label is ignored.
    ("skill-catalog:./c", "skill-catalog", "./c", None, "./c", "skill_catalog:./c"),
])
def test_parse_part(part, kind, arg, doc_source, content_ref, name):
    parsed = parse_part(part)
    assert (parsed.kind.prefix, parsed.arg, parsed.content_ref) == (kind, arg, content_ref)
    if doc_source is not None:
        assert parsed.doc_source == doc_source
    assert parsed.arm_name("LABEL") == name


@pytest.mark.parametrize("spec, match", [
    ("nonsense:foo", "Unknown arm spec"),
    ("", "Unknown arm spec"),
    ("docsy", "Unknown arm spec"),
    ("profile:", "'profile:' arm requires a path"),
    ("skill", "'skill:' arm requires a path"),
    ("skill-agent:", "'skill-agent:' arm requires a path"),
    ("skill-catalog:", "'skill-catalog:' arm requires a directory of skills"),
    ("mcp:", "'mcp:' arm requires a reference"),
    ("mcp:   ", "'mcp:' arm requires a reference"),
    ("baseline:x", "takes no argument"),
    ("docs+nonsense:x", "Unknown arm spec: 'docs\\+nonsense:x'"),
])
def test_bad_specs_are_rejected(spec, match):
    with pytest.raises(ArmSpecError, match=match):
        parse_arm_spec(spec)


def test_unknown_spec_error_lists_every_kind():
    """The message used to omit the agentic kinds, the ones most often mistyped."""
    for usage in ("'baseline'", "'docs[:<source>]'", "'mcp:<ref>'", "'profile:<path>'",
                  "'skill:<path>'", "'agent[:<source>]'", "'skill-agent:<path>'",
                  "'skill-catalog:<dir>'"):
        assert usage in VALID_SPECS
    with pytest.raises(ValueError, match="skill-agent:<path>"):
        create_treatment("nonsense:foo")


def test_a_content_named_arm_needs_its_label():
    with pytest.raises(ValueError, match="named after its content"):
        parse_part("skill:./s").arm_name()


@pytest.mark.parametrize("spec, name", [
    ("baseline", "baseline"),
    ("docs:local:./whatever", "docs"),
    ("skill:data/skills/fastcontext", "skill:fastcontext"),
    ("skill-agent:data/skills/fastcontext", "skill_agent:fastcontext"),
    ("profile:data/agent_profiles/concise_expert.md", "profile:concise_expert"),
    ("docs+skill:data/skills/fastcontext", "docs+skill:fastcontext"),
    ("skill-catalog:data/skills", "skill_catalog:data/skills"),
])
def test_built_treatments_carry_the_grammar_name(spec, name):
    """Spec to name: what `--arms` takes versus what `--baseline-arm` and reports use."""
    assert create_treatment(spec).name == name


@pytest.mark.parametrize("parts, spec", [
    (["docs", "skill:s"], "docs+skill:s"),
    (["mcp:a", "skill:s"], "skill:s+mcp:a"),
    (["mcp:a", "mcp:b", "docs"], "docs+mcp:a+mcp:b"),
    (["mcp:a", "mcp:b"], "mcp:a+mcp:b"),
])
def test_combine_specs_is_split_back_into_its_parts(parts, spec):
    assert combine_specs(parts) == spec
    if not all(p.startswith("mcp:") for p in parts):
        assert sorted(split_combined(spec)) == sorted(parts)


@pytest.mark.parametrize("name, present, expected", [
    ("docs+skill:x", {"docs", "skill:x"}, ("docs", "skill:x")),
    ("docs+skill:c++-guide", {"docs", "skill:c++-guide"}, ("docs", "skill:c++-guide")),
    ("docs+skill:x", {"docs"}, None),
    ("docs", {"docs"}, None),
    ("a+b+c", {"a", "b+c", "a+b", "c"}, ("a", "b+c")),
])
def test_split_combined_name(name, present, expected):
    assert split_combined_name(name, present) == expected


def test_content_refs_are_local_content_only_and_tolerate_bad_parts():
    spec = "nonsense:x+docs:local:./d+skill:./s+docs:url:https://x"
    assert content_refs(spec) == [("docs:local:./d", "local:./d"), ("skill:./s", "./s")]
    assert content_refs("mcp:http=x+skill:y") == []
    assert content_refs("skill-catalog:./c") == [("skill-catalog:./c", "./c")]


def test_split_arm_list():
    assert split_arm_list(" baseline, docs ,,") == ["baseline", "docs"]
    with pytest.raises(ArmSpecError, match="at least one"):
        split_arm_list(" , ")
