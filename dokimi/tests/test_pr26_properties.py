# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Properties of the arm-spec grammar that table tests do not pin (stubs only, seeded)."""

from __future__ import annotations

import random
import types
from pathlib import Path


import dokimi.mcp.factory as mcp_factory
from dokimi.eval.stats import detect_factorial_cells
from dokimi.subjects.scorecard import _digest_arm_spec
from dokimi.treatments import create_treatment
from dokimi.treatments.specs import (
    ArmSpecError,
    COMBINE,
    combine_specs,
    parse_arm_spec,
    parse_part,
    split_combined,
)


def test_a_padded_mcp_spec_is_still_never_split():
    spec = "  mcp:http=https://x/mcp?q=a+skill:y  "
    assert split_combined(spec) == ["mcp:http=https://x/mcp?q=a+skill:y"]


def test_arguments_are_stripped_and_local_is_a_prefix_of_the_ref():
    part = parse_part("docs:  local:./d ")
    assert (part.arg, part.doc_source, part.content_ref) == ("local:./d", "local:./d", "local:./d")
    assert parse_part("docs:localhost/x").content_ref is None
    assert parse_part("agent:locale:x").content_ref is None


def test_builders_pass_options_through_and_mark_mcp_arms(monkeypatch):
    calls = []

    def fake_client(source, cache_dir=None):
        calls.append((source, cache_dir))
        return types.SimpleNamespace()

    monkeypatch.setattr(mcp_factory, "create_doc_source_client", fake_client)
    cache = Path("cache-dir")
    docs = create_treatment("docs", top_k=7, rerank_threshold=0.25, cache_dir=cache)
    mcp = create_treatment("mcp:http=x", top_k=7, rerank_threshold=0.25, cache_dir=cache)
    agent = create_treatment("agent:local:./d", top_k=7, cache_dir=cache)
    assert (docs.arm_kind, mcp.arm_kind) == ("docs", "mcp_doc")
    assert (docs.top_k, mcp.top_k) == (7, 7)
    assert docs.reranker.threshold == mcp.reranker.threshold == 0.25
    assert agent.max_results == 7
    assert calls == [("context7", cache), ("mcp:http=x", cache), ("local:./d", cache)]


def test_catalog_digest_follows_catalog_content_and_mcp_parts_have_none(tmp_path):
    cat = tmp_path / "cat"
    (cat / "a").mkdir(parents=True)
    f = cat / "a" / "SKILL.md"
    f.write_text("one")
    before = _digest_arm_spec(f"skill-catalog:{cat}", base=tmp_path)
    f.write_text("two")
    assert _digest_arm_spec(f"skill-catalog:{cat}", base=tmp_path) != before
    # An mcp: spec is one part, so nothing after a '+' is read as a local path.
    mcp = "mcp:http=x+skill:" + str(cat)
    assert _digest_arm_spec(mcp, base=tmp_path) == _digest_arm_spec(mcp, base=tmp_path / "other")


_PIECES = ["", "docs", "baseline", "agent", "skill:/x", "skill-agent:y", "profile:p.md",
           "skill-catalog:/c", "docs:local:./d", "mcp:http=u?q=a", "docs-guide", "c", "-x", " ", "é", "C:\\p", "+"]


def test_split_is_lossless_and_parse_only_raises_arm_spec_error():
    rng = random.Random(26)
    for _ in range(3000):
        spec = COMBINE.join(rng.choice(_PIECES) for _ in range(rng.randint(1, 5)))
        parts = split_combined(spec)
        assert [p for p in parts if p] or not spec.strip()
        assert COMBINE.join(parts).replace(" ", "") == spec.replace(" ", "")
        try:
            parsed = parse_arm_spec(spec)
        except ArmSpecError:
            continue
        # a spec that parses re-splits to the same parts when joined again
        assert split_combined(combine_specs([p.raw for p in parsed])) == [p.raw for p in parsed] or \
            parsed[0].kind.prefix == "mcp"


def test_factorial_cells_agree_with_the_arm_name_a_combined_treatment_gets():
    arms = ["docs", "skill:c++-guide", "docs+skill:c++-guide", "baseline", "a+b", "a+b+c", "b+c"]
    assert detect_factorial_cells(arms) == [("docs", "skill:c++-guide", "docs+skill:c++-guide")]
