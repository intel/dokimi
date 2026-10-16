# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Build treatment arms from ``--arms`` spec strings.

The spec grammar — kinds, the ``+`` rule, how each arm is named — lives in
:mod:`dokimi.treatments.specs`; this module turns parsed parts into
:class:`Treatment` objects.
"""

from pathlib import Path
from typing import List, Optional

from .base import Treatment
from .arms import (
    BaselineTreatment,
    DocTreatment,
    MCPAgentTreatment,
    SkillAgentTreatment,
    SkillCatalogTreatment,
    AgentProfileTreatment,
    SkillTreatment,
    CombinedTreatment,
)
from .specs import ArmPart, parse_arm_spec


def create_treatment(
    spec: str,
    top_k: int = 5,
    rerank_threshold: float = 0.3,
    cache_dir: Optional[Path] = None,
) -> Treatment:
    """Create a single :class:`Treatment` from an arm spec.

    See :mod:`dokimi.treatments.specs` for the grammar and
    ``docs/evaluating-treatments.md`` for what each kind measures. A combined
    spec (``<spec1>+<spec2>[+...]``) becomes one :class:`CombinedTreatment`.

    Raises:
        ValueError: If the spec is unrecognised or missing an argument
            (:class:`~dokimi.treatments.specs.ArmSpecError`).
    """
    parts = parse_arm_spec(spec)
    built = [_build(p, top_k=top_k, rerank_threshold=rerank_threshold, cache_dir=cache_dir)
             for p in parts]
    return built[0] if len(built) == 1 else CombinedTreatment(built)


def _build(part: ArmPart, *, top_k: int, rerank_threshold: float,
           cache_dir: Optional[Path]) -> Treatment:
    kind = part.kind.prefix
    if kind == "baseline":
        return BaselineTreatment(name=part.arm_name())
    if kind in ("skill", "skill-agent"):
        from dokimi.skills import load_skill
        skill = load_skill(part.arg)
        cls = SkillTreatment if kind == "skill" else SkillAgentTreatment
        return cls(skill, name=part.arm_name(skill.name))
    if kind == "skill-catalog":
        from dokimi.skills import discover_skills
        if not Path(part.arg).is_dir():
            raise ValueError(f"'skill-catalog:' path is not a directory: {part.arg}")
        return SkillCatalogTreatment(discover_skills(part.arg), name=part.arm_name())
    if kind == "profile":
        from dokimi.agent_profiles import load_agent_profile
        profile = load_agent_profile(part.arg)
        return AgentProfileTreatment(profile, name=part.arm_name(profile.id))

    from dokimi.mcp.factory import create_doc_source_client
    client = create_doc_source_client(part.doc_source, cache_dir=cache_dir)
    if kind == "agent":
        return MCPAgentTreatment(client, name=part.arm_name(), max_results=top_k)
    return DocTreatment(
        client,
        name=part.arm_name(),
        top_k=top_k,
        rerank_threshold=rerank_threshold,
        arm_kind="mcp_doc" if kind == "mcp" else "docs",
    )


def create_treatments(
    specs: List[str],
    top_k: int = 5,
    rerank_threshold: float = 0.3,
    cache_dir: Optional[Path] = None,
) -> List[Treatment]:
    """Create a list of treatments, rejecting duplicate arm names."""
    treatments = [
        create_treatment(s, top_k=top_k, rerank_threshold=rerank_threshold, cache_dir=cache_dir)
        for s in specs
    ]
    seen = set()
    for t in treatments:
        if t.name in seen:
            raise ValueError(
                f"Duplicate arm name '{t.name}'. Each arm must have a unique name; "
                "load distinct profiles/skills or rename them."
            )
        seen.add(t.name)
    return treatments
