# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""When two ``skill_evidence.v1`` results may be compared: the protocol digest and the consumer checks.

A subject digest pins the bytes of a skill, not how they were measured, so two results about the
same digest can still disagree on judge, sampling, prompt or thresholds. ``protocol.digest`` names
the measurement: ``sha256`` of the canonical JSON of the *resolved* inputs that produced the number.
An input that is missing, or that names an alias (``latest``, ``default``), is left out of the
digest and listed in ``protocol.unresolved``: an alias resolves differently over time, so hashing
it would give two different measurements one digest.

The consumer side is :func:`comparability` (can this one result be merged or shown with a verdict
at all) and :func:`compare` (may these two be put side by side). The conformance fixtures under
``tests/fixtures/evidence_conformance`` pin both.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Iterable, Mapping

#: What dokimi's awareness arms measure; a change to what the digest covers bumps the version.
PROTOCOL_ID = "dokimi.awareness"
PROTOCOL_VERSION = "1"

#: A value that names a moving target rather than one snapshot. Matched as a whole trailing token,
#: so ``claude-sonnet-5-5`` is resolved and ``claude-sonnet-latest`` / ``gpt:default`` are not.
_ALIAS = re.compile(r"(?:^|[-_.:@/])(?:latest|default|auto|stable|current)$", re.IGNORECASE)

VERDICTS = ("improved", "regressed", "inconclusive")


def canonical_json(value: Any) -> bytes:
    """Sorted keys, no whitespace, UTF-8, no NaN: the bytes the protocol digest is taken over."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False).encode("utf-8")


def protocol_digest(resolved: Mapping[str, Any]) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(dict(resolved))).hexdigest()


def is_alias(value: Any) -> bool:
    return isinstance(value, str) and bool(_ALIAS.search(value.strip()))


def build_protocol(inputs: Mapping[str, Any]) -> dict[str, Any]:
    """``protocol`` for one result: id, version, digest over the resolved inputs, and what was not.

    An alias stays visible under ``aliases`` for a reader, outside the digest.
    """
    resolved: dict[str, Any] = {}
    unresolved: list[str] = []
    aliases: dict[str, str] = {}
    for key in sorted(inputs):
        value = inputs[key]
        if value is None:
            unresolved.append(key)
        elif is_alias(value):
            unresolved.append(key)
            aliases[key] = value
        else:
            resolved[key] = value
    protocol: dict[str, Any] = {
        "id": PROTOCOL_ID,
        "version": PROTOCOL_VERSION,
        "digest": protocol_digest(resolved),
        "resolved": resolved,
        "unresolved": unresolved,
    }
    if aliases:
        protocol["aliases"] = aliases
    return protocol


def recompute_verdict(result: Mapping[str, Any]) -> str | None:
    """The verdict ``verdict_rule`` gives for this result's numbers; None when the rule is unusable.

    The one basis today is ``p_adjusted``: a direction only when the corrected p is below ``alpha``
    and the delta is not zero; which direction is "improved" comes from ``higher_is_better``.
    """
    rule = result.get("verdict_rule")
    spec = result.get("metric_spec")
    if not isinstance(rule, Mapping) or not isinstance(spec, Mapping):
        return None
    alpha, better_up = rule.get("alpha"), spec.get("higher_is_better")
    if rule.get("basis") != "p_adjusted" or not _number(alpha) or not isinstance(better_up, bool):
        return None
    delta, p = result.get("delta"), result.get("p_value")
    if not _number(delta) or not _number(p) or p >= alpha or delta == 0:
        return "inconclusive"
    return "improved" if (delta > 0) == better_up else "regressed"


def comparability(result: Mapping[str, Any]) -> list[str]:
    """Why this result cannot be merged with others or shown with its verdict; empty when it can.

    A non-comparable result can still be listed. It is not "neutral": it says nothing either way.
    """
    reasons: list[str] = []
    spec = result.get("metric_spec") if isinstance(result.get("metric_spec"), Mapping) else {}
    if result.get("verdict") is not None:
        if not isinstance(result.get("verdict_rule"), Mapping):
            reasons.append("verdict without verdict_rule")
        if not spec.get("unit"):
            reasons.append("verdict without metric_spec.unit")
        if not isinstance(spec.get("higher_is_better"), bool):
            reasons.append("verdict without metric_spec.higher_is_better")
        if not reasons:
            expected = recompute_verdict(result)
            if expected is None:
                reasons.append("verdict_rule cannot be applied")
            elif expected != result.get("verdict"):
                reasons.append(f"verdict {result.get('verdict')!r} does not follow verdict_rule ({expected!r})")
    if spec.get("judged") is not False and not result.get("judge_model"):
        reasons.append("judged result without judge_model")
    protocol = result.get("protocol")
    if isinstance(protocol, Mapping) and isinstance(protocol.get("resolved"), Mapping):
        if protocol_digest(protocol["resolved"]) != protocol.get("digest"):
            reasons.append("protocol.digest does not match protocol.resolved")
    return reasons


#: Without a protocol on both sides, the fields that must be equal instead.
_FALLBACK_KEYS = ("harness", "judge_model", "metric", "verdict_rule")


def compare(result: Mapping[str, Any], reference: Mapping[str, Any]) -> list[str]:
    """Why ``result`` cannot be compared with ``reference``; empty when it can."""
    reasons = [f"result: {r}" for r in comparability(result)]
    reasons += [f"reference: {r}" for r in comparability(reference)]
    if result.get("metric") != reference.get("metric"):
        reasons.append("metric differs")
    mine, theirs = result.get("protocol"), reference.get("protocol")
    if isinstance(mine, Mapping) and isinstance(theirs, Mapping):
        if (mine.get("id"), mine.get("version")) != (theirs.get("id"), theirs.get("version")):
            reasons.append("protocol id or version differs")
        elif mine.get("digest") != theirs.get("digest"):
            changed = sorted(k for k in set(_resolved(mine)) | set(_resolved(theirs))
                             if _resolved(mine).get(k) != _resolved(theirs).get(k))
            reasons.append("protocol digest differs" + (f" ({', '.join(changed)})" if changed else ""))
        unresolved = sorted(set(mine.get("unresolved") or []) | set(theirs.get("unresolved") or []))
        if unresolved:
            reasons.append(f"protocol inputs not resolved: {', '.join(unresolved)}")
    else:
        reasons += [f"{key} differs" for key in _FALLBACK_KEYS if result.get(key) != reference.get(key)]
    return reasons


def mark_against(results: Iterable[Mapping[str, Any]],
                 reference: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Copies of ``results``, each with ``comparable`` and ``non_comparable`` set against ``reference``."""
    out = []
    for result in results:
        reasons = compare(result, reference)
        out.append({**result, "comparable": not reasons, "non_comparable": reasons})
    return out


def _resolved(protocol: Mapping[str, Any]) -> Mapping[str, Any]:
    resolved = protocol.get("resolved")
    return resolved if isinstance(resolved, Mapping) else {}


def _number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)
