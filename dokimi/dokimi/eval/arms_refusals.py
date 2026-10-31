# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Refusals for the arms runs on a CLI provider, raised before any answer is paid for.

Takes an ``argparse.Namespace`` or an :class:`~dokimi.eval.arms_run.ArmsRunConfig`: both carry
the fields read here (``provider``, ``session_turns``, ``top_k``, ``rerank_threshold``).
"""

from __future__ import annotations

from typing import Any

from dokimi.utils import InputError


def _is_agentic_arm(treatment) -> bool:
    """True when the arm offers tools, so it runs the agentic loop (see ``AgentConfig.tools``).

    The tools are added in ``prepare()``, which needs a question and may retrieve, so the
    arm types that offer tools are named here instead of probed per question.
    """
    from dokimi.plugins import PluginWrappedTreatment
    from dokimi.treatments.arms import (
        CombinedTreatment,
        MCPAgentTreatment,
        SkillAgentTreatment,
        SkillCatalogTreatment,
    )

    inner = treatment.inner if isinstance(treatment, PluginWrappedTreatment) else treatment
    if isinstance(inner, CombinedTreatment):
        return any(_is_agentic_arm(sub) for sub in inner.treatments)
    return isinstance(inner, (MCPAgentTreatment, SkillAgentTreatment, SkillCatalogTreatment))


def refuse_cli_arms(args: Any, treatments: list) -> None:
    """Refused before any answer: a CLI provider runs one prompt, with no tools and no conversation."""
    from dokimi import llm_cli

    if llm_cli.backend(args.provider) is None:
        return
    agentic = [t.name for t in treatments if _is_agentic_arm(t)]
    if agentic:
        raise InputError(f"--provider {args.provider} runs one prompt with no tools, so it cannot run "
                         f"the agentic arm(s) {', '.join(agentic)}: use an API provider for them")
    if getattr(args, "session_turns", None) is not None:
        raise InputError(f"--session-turns needs a conversation, which --provider {args.provider} "
                         "does not have: use an API provider for a session")


def refuse_cli_cell_model(matrix_cell) -> None:
    """A matrix cell sets provider and model after the parse-time model gate has read only --model."""
    from dokimi import llm_cli

    if llm_cli.backend(matrix_cell.provider) and not llm_cli.accepts_model(matrix_cell.provider,
                                                                          matrix_cell.model):
        raise InputError(f"matrix cell {matrix_cell.name!r}: provider {matrix_cell.provider} takes a "
                         f"Claude model (sonnet, haiku, opus or a claude-* id), not {matrix_cell.model!r}")


def refuse_cli_cell(args: Any, specs: list[str]) -> None:
    """A matrix cell on a CLI provider with an agentic arm is refused before the first cell is answered."""
    from dokimi import llm_cli
    from dokimi.treatments import create_treatments

    if llm_cli.backend(args.provider) is not None:
        refuse_cli_arms(args, create_treatments(specs, top_k=args.top_k, rerank_threshold=args.rerank_threshold))
