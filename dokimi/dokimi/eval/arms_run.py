# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""What an arms run checks and resolves before the first answer is paid for.

The arm-run commands (``arms run``, ``arms matrix-run``, and ``subjects run`` through
``matrix-run``) and ``eval`` judge-reliability share these: the doc arm's library id, the
``expected_skill`` labels, the harnesses a matrix cell may name, and the sampling probe.
"""

from __future__ import annotations

import functools

from dokimi.eval.cells import HARNESS_ARMS_RUNNER
from dokimi.utils import question_id


def validate_supported_matrix_harnesses(cells) -> None:
    """Reject matrix harness labels that the in-process runner cannot execute."""
    unsupported = [
        cell.harness
        for cell in cells
        if cell.harness != HARNESS_ARMS_RUNNER
    ]
    if unsupported:
        raise ValueError(
            "matrix cells only support harness='arms-runner' until other runners are wired; "
            "unsupported harnesses: "
            + ", ".join(sorted(set(unsupported)))
        )


def resolve_doc_library_id(treatments, product: str, explicit_id=None, registry=None):
    """Resolve a library id for the first doc arm, before plugin wrapping.

    Same three-step order as the ``answers``/``questions`` commands: explicit
    flag, then the product's ``context7_id`` in ``products.yaml``, then the
    client's own resolution. When no id can be named the ``ValueError`` is
    raised to the caller, which stops the run: substituting the product name
    would query an id that does not exist and record an empty-retrieval doc
    arm as a measurement. Other failures (an unreachable MCP server, say) still
    fall back to the product name and are reported by the arm itself.
    """
    if explicit_id is not None:
        return explicit_id

    from dokimi.mcp.factory import resolve_library_id_for_product
    from dokimi.treatments.arms import DocTreatment

    for treatment in treatments:
        unwrapped = getattr(treatment, "inner", treatment)
        if isinstance(unwrapped, DocTreatment):
            try:
                return resolve_library_id_for_product(
                    product, unwrapped.mcp_client, registry=registry
                )
            except ValueError:
                raise
            except Exception:
                return product
    return None


def _offered_skill_names(treatments) -> set:
    """The names of the skills any arm in *treatments* offers, past plugins and stacks."""
    from dokimi.treatments import SkillAgentTreatment, SkillCatalogTreatment

    def leaves(t):
        t = getattr(t, "inner", t)
        for sub in getattr(t, "treatments", None) or [t]:
            yield sub

    known: set = set()
    for t in treatments:
        for sub in leaves(t):
            if isinstance(sub, SkillCatalogTreatment):
                known.update(s.name for s in sub.skills)
            elif isinstance(sub, SkillAgentTreatment):
                known.add(sub.skill.name)
    return known


def validate_expected_skills(questions: list[dict], treatments) -> None:
    """Refuse ``expected_skill`` labels the selection summary cannot score.

    Checked before any call is spent: a label of the wrong type crashes the
    summary after the run, and a name no skill arm offers silently scores as a
    miss. Names are checked only when some arm offers skills.
    """
    known = _offered_skill_names(treatments)
    for i, q in enumerate(questions):
        if "expected_skill" not in q:
            continue
        label = q["expected_skill"]
        qid = question_id(q)
        qid = f"#{i}" if qid is None else qid
        names = label if isinstance(label, list) else [label] if label is not None else []
        if not all(isinstance(n, str) and n for n in names):
            raise ValueError(f"question {qid}: expected_skill must be a skill name, a list of "
                             f"names, or null; got {label!r}")
        unknown = [n for n in names if known and n not in known]
        if unknown:
            raise ValueError(f"question {qid}: expected_skill names no offered skill: "
                             f"{', '.join(unknown)}")


@functools.lru_cache(maxsize=None)
def probe_sampling(model: str, provider: str, temperature, seed) -> None:
    """One short real call with the requested sampling, so a value the provider
    rejects (gpt-5 accepts only temperature=1) fails before the run, not per answer."""
    if temperature is None and seed is None:
        return
    from dokimi.llm import llm_call_with_usage

    try:
        llm_call_with_usage("Reply with OK.", model=model, provider=provider,
                            max_tokens=16, temperature=temperature, seed=seed)
    except Exception as exc:
        raise ValueError(
            f"Probe call to '{provider}/{model}' with temperature={temperature}, "
            f"seed={seed} failed, so every call in the run would too: {exc}"
        ) from exc
