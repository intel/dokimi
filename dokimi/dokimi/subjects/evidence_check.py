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

from dokimi.eval.judge_context import PROTOCOL_MIXED
from dokimi.measure import as_float

#: What dokimi's awareness arms measure; a change to what the digest covers bumps the version.
PROTOCOL_ID = "dokimi.awareness"
PROTOCOL_VERSION = "2"

#: A value that names a moving target rather than one snapshot. Matched as a whole trailing token,
#: so ``claude-sonnet-5-5`` is resolved and ``claude-sonnet-latest`` / ``gpt:default`` are not.
_ALIAS = re.compile(r"(?:^|[-_.:@/])(?:latest|default|auto|stable|current)$", re.IGNORECASE)
#: The 1M-context suffix a CLI takes after an alias (``opus[1m]``); it does not make the name a snapshot.
_CONTEXT_SUFFIX = re.compile(r"\[1m\]$", re.IGNORECASE)
#: Bare family names a harness resolves to whatever it ships today (dokimi's own claude-cli judges
#: default to ``opus``/``sonnet``); a dated or versioned id is a snapshot.
_FLOATING = re.compile(r"^(?:claude|opus|sonnet|haiku|gpt-4o(?:-mini)?|gpt-4|gpt-5|o1|o3|o4-mini)$",
                       re.IGNORECASE)

VERDICTS = ("improved", "regressed", "inconclusive")


def _normal(value: Any) -> Any:
    # An integral float is written as an int, so 0 and 0.0 (or 3 and 3.0) are one measurement.
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, Mapping):
        return {str(k): _normal(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_normal(v) for v in value]
    return value


def canonical_json(value: Any) -> bytes:
    """Sorted keys, no whitespace, UTF-8, no NaN, integral floats as ints: the bytes the digest is over."""
    return json.dumps(_normal(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False).encode("utf-8")


def _encodable(value: Any) -> bool:
    try:
        canonical_json(value)
    except (TypeError, ValueError):
        return False
    return True


def protocol_digest(resolved: Mapping[str, Any]) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(dict(resolved))).hexdigest()


def is_alias(value: Any) -> bool:
    """A moving target: an alias suffix, a bare family name, or either anywhere inside a nested value."""
    if isinstance(value, str):
        text = _CONTEXT_SUFFIX.sub("", value.strip())
        return bool(_ALIAS.search(text) or _FLOATING.match(text.rsplit("/", 1)[-1]))
    if isinstance(value, Mapping):
        return any(is_alias(v) for v in value.values())
    if isinstance(value, (list, tuple)):
        return any(is_alias(v) for v in value)
    return False


def build_protocol(inputs: Mapping[str, Any]) -> dict[str, Any]:
    """``protocol`` for one result: id, version, digest over the resolved inputs, and what was not.

    An alias stays visible under ``aliases`` for a reader, outside the digest.
    """
    resolved: dict[str, Any] = {}
    unresolved: list[str] = []
    aliases: dict[str, str] = {}
    for key in sorted(inputs):
        value = inputs[key]
        if value is None or value == PROTOCOL_MIXED:  # mixed: some scores saw other inputs than the rest
            unresolved.append(key)
        elif not _encodable(value):  # NaN or infinity: no bytes to hash, so the input stays out of the digest
            unresolved.append(key)
        elif is_alias(value):
            unresolved.append(key)
            aliases[key] = value if isinstance(value, str) else canonical_json(value).decode("utf-8")
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
    alpha, better_up = as_float(rule.get("alpha")), spec.get("higher_is_better")
    if rule.get("basis") != "p_adjusted" or alpha is None or not isinstance(better_up, bool):
        return None
    if not 0 < alpha < 1:
        return None  # alpha is a probability: outside (0, 1) the rule says nothing about a verdict
    if not _valid_decimals(rule.get("p_decimals"), allow_none=True):
        return None  # an unsupported rounding precision: the rule cannot be applied
    delta, p = as_float(result.get("delta")), as_float(result.get("p_value"))
    if p is not None and not 0 <= p <= 1:
        return None  # a p-value is a probability: an out-of-range one cannot be on either side of alpha
    if p is not None and _near_alpha(p, alpha, rule.get("p_decimals")):
        return None  # the rounded p cannot say which side of alpha the unrounded one was on
    if delta is None or p is None or p >= alpha or delta == 0:
        return "inconclusive"
    return "improved" if (delta > 0) == better_up else "regressed"


def comparability(result: Mapping[str, Any]) -> list[str]:
    """Why this result cannot be merged with others or shown with its verdict; empty when it can.

    A non-comparable result can still be listed. It is not "neutral": it says nothing either way.
    """
    reasons: list[str] = []
    stated = result.get("metric_spec")
    spec: Mapping[str, Any] = stated if isinstance(stated, Mapping) else {}
    if result.get("verdict") is not None:
        reasons += _verdict_reasons(result, spec)
    if spec.get("judged") is not False and not result.get("judge_model"):
        reasons.append("judged result without judge_model")
    protocol = result.get("protocol")
    if isinstance(protocol, Mapping) and isinstance(protocol.get("resolved"), Mapping):
        try:
            digest = protocol_digest(protocol["resolved"])
        except (TypeError, ValueError):  # NaN, infinity or a non-JSON value: no digest to check
            reasons.append("protocol.resolved is not JSON, so its digest cannot be checked")
        else:
            if digest != protocol.get("digest"):
                reasons.append("protocol.digest does not match protocol.resolved")
        reasons += _disagreements(result, protocol["resolved"])
    return reasons


def _field(result: Mapping[str, Any], field: str) -> Any:
    parent, _, child = field.partition(".")
    value = result.get(parent)
    if child:
        return value.get(child) if isinstance(value, Mapping) else None
    return value


def _disagreements(result: Mapping[str, Any], resolved: Mapping[str, Any]) -> list[str]:
    """A result may omit a field, but any stated value must match its protocol input."""
    out = []
    for field, key in _AGREES.items():
        value = _field(result, field)
        if value is not None and key in resolved and resolved[key] != value:
            out.append(f"{field} differs from protocol.resolved.{key}")
    return out


def _verdict_reasons(result: Mapping[str, Any], spec: Mapping[str, Any]) -> list[str]:
    reasons = []
    if not isinstance(result.get("verdict_rule"), Mapping):
        reasons.append("verdict without verdict_rule")
    if not spec.get("unit"):
        reasons.append("verdict without metric_spec.unit")
    if not isinstance(spec.get("higher_is_better"), bool):
        reasons.append("verdict without metric_spec.higher_is_better")
    if reasons or not isinstance(result.get("verdict_rule"), Mapping):
        return reasons
    expected = recompute_verdict(result)
    rule = result["verdict_rule"]
    p, alpha = as_float(result.get("p_value")), as_float(rule.get("alpha"))
    if expected is None and p is not None and alpha is not None and _near_alpha(
            p, alpha, rule.get("p_decimals")):
        return ["p_value is within its rounding of alpha: the verdict cannot be recomputed"]
    if expected is None:
        return ["verdict_rule cannot be applied"]
    if expected != result.get("verdict"):
        return [f"verdict {result.get('verdict')!r} does not follow verdict_rule ({expected!r})"]
    return []


#: Without a protocol on both sides, the fields that must be equal instead.
_FALLBACK_KEYS = ("harness", "judge_model", "judge_provider", "metric", "verdict_rule",
                  "p_correction", "ci.confidence", "ci.method")
#: The inputs a ``dokimi.awareness`` version 2 protocol names; one it leaves out is unresolved.
REQUIRED_INPUTS = ("alpha", "ci_confidence", "correction", "eval_set_digest", "family_tested", "judge_context",
                   "judge_model", "judge_prompt_sha256", "judge_provider", "judge_sampling", "metric",
                   "repeats", "test")
#: Fields a result states that its protocol resolves too: a digest over the resolved inputs cannot
#: catch a result that states another value for them.
_AGREES = {
    "judge_model": "judge_model", "judge_provider": "judge_provider", "metric": "metric",
    "verdict_rule.alpha": "alpha", "verdict_rule.correction": "correction", "verdict_rule.test": "test",
    "p_correction": "correction", "ci.confidence": "ci_confidence", "ci.method": "test",
}


def compare(result: Mapping[str, Any], reference: Mapping[str, Any]) -> list[str]:
    """Why ``result`` cannot be compared with ``reference``; empty when it can."""
    reasons = [f"result: {r}" for r in comparability(result)]
    reasons += [f"reference: {r}" for r in comparability(reference)]
    if result.get("metric") != reference.get("metric"):
        reasons.append("metric differs")
    if result.get("metric_spec") != reference.get("metric_spec"):
        reasons.append("metric specification differs (unit, range or direction)")
    mine, theirs = result.get("protocol"), reference.get("protocol")
    if isinstance(mine, Mapping) and isinstance(theirs, Mapping):
        reasons += _protocol_reasons(mine, theirs)
        # harness is not a protocol input, so a digest over the inputs cannot tell two harnesses apart
        if result.get("harness") != reference.get("harness"):
            reasons.append("harness differs")
    elif isinstance(mine, Mapping) or isinstance(theirs, Mapping):
        reasons.append("protocol on one side only: the fallback fields cannot stand for its inputs")
    else:
        reasons += [f"{key} differs" for key in _FALLBACK_KEYS if _field(result, key) != _field(reference, key)]
    return reasons


def _protocol_reasons(mine: Mapping[str, Any], theirs: Mapping[str, Any]) -> list[str]:
    reasons = []
    if (mine.get("id"), mine.get("version")) != (theirs.get("id"), theirs.get("version")):
        reasons.append("protocol id or version differs")
    elif (mine.get("id"), mine.get("version")) != (PROTOCOL_ID, PROTOCOL_VERSION):
        # an unknown protocol's required inputs cannot be checked, so equal digests prove nothing
        reasons.append("protocol id or version unknown: its required inputs cannot be checked")
    elif mine.get("digest") != theirs.get("digest"):
        changed = sorted(k for k in set(_resolved(mine)) | set(_resolved(theirs))
                         if _resolved(mine).get(k) != _resolved(theirs).get(k))
        reasons.append("protocol digest differs" + (f" ({', '.join(changed)})" if changed else ""))
    unresolved = sorted(_unresolved(mine) | _unresolved(theirs))
    if unresolved:
        reasons.append(f"protocol inputs not resolved: {', '.join(unresolved)}")
    return reasons


def _unresolved(protocol: Mapping[str, Any]) -> set[str]:
    """The inputs the protocol lists as unresolved, the ones the current version omits, and
    any resolved value that is null or an alias: a digest over them identifies no fixed measurement."""
    listed = set(protocol.get("unresolved") or [])
    if (protocol.get("id"), protocol.get("version")) == (PROTOCOL_ID, PROTOCOL_VERSION):
        listed |= set(REQUIRED_INPUTS) - set(_resolved(protocol))
    listed |= {key for key, value in _resolved(protocol).items() if value in (None, PROTOCOL_MIXED) or is_alias(value)}
    return listed


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


def _near_alpha(p: float, alpha: float, decimals: Any) -> bool:
    if not _valid_decimals(decimals):
        return False
    return abs(p - alpha) <= 0.5 * 10 ** -decimals + 1e-12


#: The precisions a rounded p can carry: a float holds about 15 significant digits.
_MAX_DECIMALS = 15


def _valid_decimals(value: Any, *, allow_none: bool = False) -> bool:
    if value is None:
        return allow_none
    return isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= _MAX_DECIMALS
