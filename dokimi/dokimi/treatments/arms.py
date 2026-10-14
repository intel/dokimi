# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Concrete treatment arms for each evaluation scenario."""

import logging
from typing import Any, Dict, List, Optional

from dokimi.agent_profiles import AgentProfile
from dokimi.skills import Skill
from dokimi.utils import digest_text

from .base import AgentConfig, Treatment
from .specs import combined_name

logger = logging.getLogger(__name__)


class BaselineTreatment(Treatment):
    """No augmentation — the control arm (equivalent to ``without_docs``)."""

    arm_kind = "baseline"

    def __init__(self, name: str = "baseline"):
        self.name = name

    def prepare(self, question_text, library_name, library_id=None) -> AgentConfig:
        return AgentConfig(metadata={"arm_kind": "baseline"})


class DocTreatment(Treatment):
    """Documentation injection via an ``MCPClient`` (the original ``with_docs``).

    Retrieves chunks for the question, reranks them, and injects the top
    survivors as context. Works for any doc source — local files, a URL, the
    Context7 HTTP API, or a real MCP server — since they all implement the same
    ``MCPClient`` retrieval contract.
    """

    def __init__(
        self,
        mcp_client,
        name: str = "docs",
        top_k: int = 5,
        rerank_threshold: float = 0.3,
        keep: int = 3,
        reranker=None,
        arm_kind: str = "docs",
    ):
        self.name = name
        self.mcp_client = mcp_client
        self.top_k = top_k
        self.keep = keep
        self.arm_kind = arm_kind
        self.reranker = reranker or self._default_reranker(rerank_threshold)

    @staticmethod
    def _default_reranker(threshold: float):
        from dokimi.eval.reranker import (
            SimpleReranker,
            SentenceTransformerReranker,
            SENTENCE_TRANSFORMERS_AVAILABLE,
        )

        if SENTENCE_TRANSFORMERS_AVAILABLE:
            try:
                return SentenceTransformerReranker(threshold=threshold)
            except Exception as exc:  # pragma: no cover - env dependent
                logger.warning("Semantic reranker unavailable (%s); using lexical", exc)
        return SimpleReranker(threshold=threshold)

    def prepare(self, question_text, library_name, library_id=None) -> AgentConfig:
        lib_id = library_id or library_name
        metadata: Dict[str, Any] = {
            "arm_kind": self.arm_kind,
            "raw_count": 0,
            "after_rerank": 0,
            "top_score": None,
        }
        try:
            raw_docs = self.mcp_client.get_library_docs(
                library_id=lib_id, query=question_text, max_results=self.top_k
            )
        except Exception:
            logger.exception("Context retrieval failed for arm %s", self.name)
            metadata["error"] = "retrieval_failed"
            return AgentConfig(metadata=metadata)

        metadata["raw_count"] = len(raw_docs)
        if not raw_docs:
            return AgentConfig(metadata=metadata)

        try:
            reranked = self.reranker.rerank(question_text, raw_docs)
        except Exception:
            logger.exception("Rerank failed for arm %s; using raw docs", self.name)
            metadata["error"] = "rerank_failed"
            reranked = raw_docs
        metadata["after_rerank"] = len(reranked)
        if not reranked:
            return AgentConfig(metadata=metadata)

        scores = [d.get("relevance_score", 0) for d in reranked]
        metadata["top_score"] = round(max(scores), 3) if scores else None
        return AgentConfig(injected_context=reranked[: self.keep], metadata=metadata)


class MCPAgentTreatment(Treatment):
    """Agentic MCP doc use — offer a doc-search tool; the model decides to call it.

    This is the faithful test of whether a doc source is *usable by an agent*,
    not just whether its text is relevant: the model must choose good queries
    and integrate the results. Contrast with :class:`DocTreatment`, which
    pre-injects retrieved chunks.
    """

    def __init__(
        self,
        mcp_client,
        name: str = "mcp_agent",
        max_results: int = 5,
        keep: int = 3,
        arm_kind: str = "mcp_agent",
    ):
        self.name = name
        self.mcp_client = mcp_client
        self.max_results = max_results
        self.keep = keep
        self.arm_kind = arm_kind

    def prepare(self, question_text, library_name, library_id=None) -> AgentConfig:
        from .tools import DocQueryTool

        lib_id = library_id or library_name
        tool = DocQueryTool(
            self.mcp_client, lib_id, max_results=self.max_results, keep=self.keep
        )
        return AgentConfig(
            tools=[tool],
            metadata={"arm_kind": self.arm_kind, "library_id": lib_id},
        )


class SkillAgentTreatment(Treatment):
    """Agentic skill use — offer the skill via progressive disclosure.

    The model sees only the skill's name + one-line description (in the tool
    schema) and must *decide* to load the full instructions, mirroring how
    skills are actually consumed. Running the skill's bundled scripts needs a
    sandbox and stays on the terminal-bench task track.
    """

    arm_kind = "skill_agent"

    def __init__(self, skill: Skill, name: Optional[str] = None, max_chars: int = 12_000):
        self.skill = skill
        self.max_chars = max_chars
        self.name = name or f"skill_agent:{skill.name}"

    def prepare(self, question_text, library_name, library_id=None) -> AgentConfig:
        from .tools import ViewSkillTool

        return AgentConfig(
            tools=[ViewSkillTool(self.skill, self.max_chars)],
            # Nothing is injected, so the judge's reference has to carry the
            # skill itself as well as its manifest: the transcript keeps only a
            # 200-char preview of each tool result, so what the agent actually
            # read is not recoverable at judge time.
            judge_reference="\n\n".join(
                p for p in (self.skill.as_context(self.max_chars),
                            self.skill.resource_manifest()) if p
            ),
            metadata={
                "arm_kind": "skill_agent",
                "skill_name": self.skill.name,
                "skill_path": str(self.skill.path),
            },
        )


class SkillCatalogTreatment(Treatment):
    """Skill selection — offer a whole catalog and record which skill the model loads.

    One progressive-disclosure tool per skill, so a question is answered in the
    presence of every rival the catalog holds. Which skill the model loads first
    is compared with the question's ``expected_skill`` (``None`` = no skill should
    load) by :func:`dokimi.eval.arm_summary.summarize_skill_selection`.

    This is a tool-calling proxy, not a coding harness: a real harness renders the
    catalog as one listing it may truncate, and lets the model explore before it
    decides. Real-harness selection is measured with an external tool against the
    trigger set ``catalog trigger-set`` writes (docs/evaluating-treatments.md).
    """

    arm_kind = "skill_catalog"

    def __init__(self, skills: List[Skill], name: Optional[str] = None, max_chars: int = 12_000):
        if not skills:
            raise ValueError("skill catalog is empty")
        from .tools import ViewSkillTool

        # The model tells skills apart by tool name, which sanitises the skill name:
        # `a-b` and `a_b` collide there, and providers cap function names at 64 chars.
        tools = [ViewSkillTool(s, max_chars) for s in skills]
        names = [t.name for t in tools]
        dupes = sorted({n for n in names if names.count(n) > 1})
        if dupes:
            raise ValueError(f"skill catalog has duplicate tool names: {', '.join(dupes)}")
        too_long = [n for n in names if len(n) > 64]
        if too_long:
            raise ValueError(f"skill tool names over the 64-char function-name limit: {', '.join(too_long)}")
        self.skills = skills
        self.max_chars = max_chars
        self.name = name or f"skill_catalog:{len(skills)}"

    def _judge_reference_for(self, loaded: List[str]) -> str:
        """Loaded skills' bodies (budget split evenly) + every catalog manifest."""
        from dokimi.eval.judge_context import MAX_JUDGE_REFERENCE_CHARS

        by_name = {s.name: s for s in self.skills}
        manifests = "\n\n".join(m for s in self.skills if (m := s.resource_manifest()))
        picked = [by_name[n] for n in loaded if n in by_name]
        if not picked:
            return manifests
        # Each loaded skill keeps an equal share, so a second load cannot push
        # the first out of the judge's view the way a whole-string cut does.
        share = max(0, MAX_JUDGE_REFERENCE_CHARS - len(manifests) - 2) // len(picked) - 2
        bodies = []
        for s in picked:
            text = s.as_context(self.max_chars)
            bodies.append(text if len(text) <= share else text[:max(0, share - 2)].rstrip() + "\n…")
        return "\n\n".join(bodies + ([manifests] if manifests else []))

    def prepare(self, question_text, library_name, library_id=None) -> AgentConfig:
        from .tools import ViewSkillTool

        return AgentConfig(
            tools=[ViewSkillTool(s, self.max_chars) for s in self.skills],
            # A whole catalog does not fit the judge's cap (intel/skills: 285k
            # chars vs 20k, so the judge saw the alphabetically first 2-3 skills).
            # The judge gets every manifest (paths are in the tool schemas too)
            # plus the body of each skill the agent loaded, which the tool serves
            # deterministically, so the transcript's 200-char previews suffice.
            judge_reference_for_loaded=self._judge_reference_for,
            metadata={
                "arm_kind": "skill_catalog",
                "catalog_size": len(self.skills),
                "catalog_skills": [s.name for s in self.skills],
                # What a harness listing of this catalog would cost: the number a
                # listing budget is compared against (Claude Code shows bare names
                # once its budget is exceeded).
                "catalog_listing_chars": sum(len(s.name) + len(s.description) for s in self.skills),
            },
        )


class AgentProfileTreatment(Treatment):
    """Agent persona prompt — swaps the answering agent's system prompt."""

    arm_kind = "agent_profile"

    def __init__(
        self,
        profile: AgentProfile,
        name: Optional[str] = None,
        max_chars: int = 12_000,
    ):
        self.profile = profile
        self.max_chars = max_chars
        self.name = name or f"profile:{profile.id}"

    def prepare(self, question_text, library_name, library_id=None) -> AgentConfig:
        prompt = self.profile.as_prompt(self.max_chars)
        source_chars = len(self.profile.system_prompt.strip())
        # Mirror the loader's condition rather than comparing lengths: the cap appends an
        # ellipsis, so a prompt one char over the cap comes back *longer* than the source
        # and a length comparison would report it as untruncated.
        truncated = source_chars > self.max_chars
        if truncated:
            logger.warning(
                "Agent profile '%s' is %d chars and was truncated to the %d-char cap; "
                "the arm measures the truncated prompt.",
                self.profile.path, source_chars, self.max_chars,
            )
        return AgentConfig(
            system_prompt=prompt,
            metadata={
                "arm_kind": "agent_profile",
                "profile_id": self.profile.id,
                "profile_name": self.profile.name,
                "profile_path": str(self.profile.path),
                # Digest the rendered text, not the file: an edit that leaves the file
                # the same length still moves the digest, and capping moves it too, which
                # is how a batch gate notices that two cells measured different prompts.
                # This is the profile's *contribution*, not necessarily the whole prompt
                # sent — a prompt-middleware plugin appends to it afterwards, and
                # ``PluginWrappedTreatment`` records ``system_prompt_sha256`` for that.
                "profile_prompt_sha256": digest_text(prompt),
                "profile_prompt_chars": len(prompt),
                "profile_prompt_chars_source": source_chars,
                "profile_prompt_truncated": truncated,
            },
        )


class SkillTreatment(Treatment):
    """Agent skill injected as context (skill-as-context evaluation mode).

    This is the cheap, single-shot mode: the skill body is injected like a doc
    chunk. The faithful agentic mode (the agent decides to invoke the skill and
    run its bundled scripts) belongs on the terminal-bench task track.
    """

    arm_kind = "skill"

    def __init__(self, skill: Skill, name: Optional[str] = None, max_chars: int = 12_000):
        self.skill = skill
        self.max_chars = max_chars
        self.name = name or f"skill:{skill.name}"

    def prepare(self, question_text, library_name, library_id=None) -> AgentConfig:
        content = self.skill.as_context(self.max_chars)
        # `as_context` truncates silently. An unrecorded truncation is how a skill arm
        # ends up measuring three quarters of a skill while the artifact reads as if it
        # measured all of it, so record it on the same terms as the profile arm.
        source_chars = len(self.skill.body.strip())
        truncated = source_chars > self.max_chars
        if truncated:
            logger.warning(
                "Skill '%s' body is %d chars and was truncated to the %d-char cap; "
                "the arm measures the truncated skill.",
                self.skill.path, source_chars, self.max_chars,
            )
        chunk = {
            "content": content,
            "source": f"skill:{self.skill.name}",
            "relevance_score": 1.0,
        }
        return AgentConfig(
            injected_context=[chunk],
            # The skill body is the same for every question, so it is a genuine
            # cacheable prefix — unlike DocTreatment's query-driven chunks.
            context_is_static=True,
            # The body is already injected; only the inventory of bundled files
            # is missing, and that is exactly what the judge cannot verify.
            judge_reference=self.skill.resource_manifest(),
            metadata={
                "arm_kind": "skill",
                "skill_name": self.skill.name,
                "skill_path": str(self.skill.path),
                # Three sizes, because two of them are easy to confuse. `skill_chars` is
                # the body after the cap, so it pairs with `skill_chars_source` the way
                # `profile_prompt_chars` pairs with its source and the difference is what
                # the cap cut. `skill_chars_injected` is the whole rendered block, header
                # and description included — which is why it exceeds the source for a
                # skill that was never truncated, and why it is not the number to compare
                # against the cap.
                "skill_chars": min(source_chars, self.max_chars),
                "skill_chars_source": source_chars,
                "skill_chars_injected": len(content),
                "skill_truncated": truncated,
            },
        )


class CombinedTreatment(Treatment):
    """Combine multiple treatments into a single arm (e.g., docs + skill).

    This treatment merges the contexts, tools, and metadata from multiple
    sub-treatments, allowing evaluation of combined approaches (e.g., "does
    docs + skill together outperform skill alone?").

    The sub-treatments are prepared independently, then their outputs are merged:
    - injected_context: concatenated in the order the arm spec lists them
    - tools: concatenated (for agentic arms)
    - system_prompt: first non-None value wins
    - metadata: tracks all sub-treatment metadata

    Example spec: ``docs+skill:data/skills/fastcontext``
    """

    def __init__(self, treatments: List[Treatment], name: Optional[str] = None):
        self.treatments = treatments
        self.name = name or combined_name(t.name for t in treatments)

    def prepare(self, question_text, library_name, library_id=None) -> AgentConfig:
        # Prepare all sub-treatments independently
        configs = [
            t.prepare(question_text, library_name, library_id) for t in self.treatments
        ]

        # Merge injected contexts in spec order — `docs+skill:x` and `skill:x+docs`
        # therefore send the same blocks in a different order, which is the caller's choice.
        merged_context = []
        for cfg in configs:
            merged_context.extend(cfg.injected_context)

        # Merge tools (for agentic arms - rare, but supported)
        merged_tools = []
        for cfg in configs:
            merged_tools.extend(cfg.tools)
        # Two sub-arms can offer the same tool (a catalog plus a skill-agent arm for one
        # of its skills); duplicate function names make selection ambiguous and most
        # providers reject them.
        tool_names = [t.name for t in merged_tools]
        dupes = sorted({n for n in tool_names if tool_names.count(n) > 1})
        if dupes:
            raise ValueError(f"combined arm '{self.name}' offers duplicate tools: {', '.join(dupes)}")

        # Merge metadata - track each sub-treatment's contribution
        merged_metadata = {
            "arm_kind": "combined",
            "combined_count": len(self.treatments),
            "treatments": [],
        }
        for t, cfg in zip(self.treatments, configs, strict=True):
            merged_metadata["treatments"].append(
                {"name": t.name, "arm_kind": cfg.metadata.get("arm_kind"), **cfg.metadata}
            )

        # System prompt: first non-None wins (profiles rarely combined with docs/skills).
        # More than one means the later ones never reach the model, which would make
        # the arm's metadata claim a treatment the answer never saw — warn loudly.
        with_prompt = [t.name for t, c in zip(self.treatments, configs, strict=True) if c.system_prompt]
        if len(with_prompt) > 1:
            logger.warning(
                "Combined arm '%s' supplies %d system prompts (%s); only '%s' is sent — "
                "the rest are dropped. Split them into separate arms to measure both.",
                self.name, len(with_prompt), ", ".join(with_prompt), with_prompt[0],
            )
            merged_metadata["dropped_system_prompts"] = with_prompt[1:]
        system_prompt = next((c.system_prompt for c in configs if c.system_prompt), None)

        # A prefix is only reusable if *everything* in it is; one query-driven
        # doc chunk anywhere in the merged context makes the whole block vary.
        context_is_static = all(
            c.context_is_static for c in configs if c.injected_context
        )

        # A loaded-skill reference (catalog sub-arm) composes with the static ones.
        dynamic = [c.judge_reference_for_loaded for c in configs
                   if c.judge_reference_for_loaded is not None and c.is_agentic]
        static_ref = "\n\n".join(
            c.judge_reference for c in configs
            if c.judge_reference and (c.is_agentic or not merged_tools)
        )

        def reference_for_loaded(loaded: List[str]) -> str:
            return "\n\n".join(p for p in [static_ref] + [f(loaded) for f in dynamic] if p)

        return AgentConfig(
            system_prompt=system_prompt,
            injected_context=merged_context,
            tools=merged_tools,
            context_is_static=context_is_static,
            judge_reference_for_loaded=reference_for_loaded if dynamic else None,
            # The reference is what the judge may treat as available to the
            # arm. On the plain path every sub-treatment's material is. On the
            # agentic path only the agentic sub-treatments' is: that path does
            # not put injected context in front of the agent, so a non-agentic
            # skill's manifest names files the agent has no tool to reach.
            judge_reference=static_ref,
            metadata=merged_metadata,
        )
