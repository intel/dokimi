# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Refusals for the arms commands on a CLI provider, raised before any answer is paid for."""

from __future__ import annotations

import argparse


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


def refuse_cli_arms(args: argparse.Namespace, treatments: list) -> None:
    """Refused before any answer: a CLI provider runs one prompt, with no tools and no conversation."""
    from dokimi import llm_cli

    if llm_cli.backend(args.provider) is None:
        return
    agentic = [t.name for t in treatments if _is_agentic_arm(t)]
    if agentic:
        raise ValueError(f"--provider {args.provider} runs one prompt with no tools, so it cannot run "
                         f"the agentic arm(s) {', '.join(agentic)}: use an API provider for them")
    if getattr(args, "session_turns", None) is not None:
        raise ValueError(f"--session-turns needs a conversation, which --provider {args.provider} "
                         "does not have: use an API provider for a session")


