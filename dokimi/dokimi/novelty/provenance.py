# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Where a novelty number came from: the code, the artifact, the prompts and the models.

The ``provenance`` block lets a figure quoted from a novelty JSON be traced to the dokimi
build, the measured text and the prompt wording that produced it. The vendor fields extend
the same-model self-judge refusal to a warning for a judge of the answerer's vendor.
"""

from __future__ import annotations

import functools
import hashlib
from pathlib import Path

from dokimi import __version__

# Substring of a lowercased model id -> vendor; first match wins. The CLI aliases
# (opus, sonnet, haiku) are Claude models.
_VENDOR_MARKERS = (
    ("claude", "anthropic"), ("anthropic", "anthropic"), ("opus", "anthropic"),
    ("sonnet", "anthropic"), ("haiku", "anthropic"),
    ("gpt", "openai"), ("openai", "openai"),
    ("grok", "xai"), ("xai", "xai"),
    ("gemini", "google"), ("google", "google"),
    ("nemotron", "nvidia"), ("nvidia", "nvidia"),
    ("llama", "meta"), ("meta", "meta"),
)
UNKNOWN_VENDOR = "unknown"


def vendor_of(model: str | None) -> str:
    """The vendor a model id names (``us.anthropic.claude-...`` -> ``anthropic``), else ``unknown``."""
    lowered = (model or "").lower()
    return next((vendor for marker, vendor in _VENDOR_MARKERS if marker in lowered), UNKNOWN_VENDOR)


def vendor_record(answerer_model: str, judge_model: str) -> dict:
    """``{answerer_vendor, judge_vendor, same_vendor}``; ``same_vendor`` is False when either is unknown."""
    answerer, judge = vendor_of(answerer_model), vendor_of(judge_model)
    return {
        "answerer_vendor": answerer,
        "judge_vendor": judge,
        "same_vendor": answerer == judge != UNKNOWN_VENDOR,
    }


def same_vendor_warning(record: dict) -> str | None:
    """The warning for a judge of the answerer's vendor, or ``None``."""
    if not record.get("same_vendor"):
        return None
    return (f"the judge and the answerer are both {record['judge_vendor']} models: a judge can "
            "favour answers of its own vendor; pass a --judge-model of another vendor")


def prompts_sha256() -> str:
    """Digest of every novelty stage prompt, so a prompt edit is visible as a different measurement."""
    from . import atoms, classify, measure, needs, probes, retention

    mods = (atoms, classify, measure, needs, probes, retention)
    texts = sorted(v for m in mods for k, v in vars(m).items()
                   if k.endswith(("_PROMPT", "_SYSTEM")) and isinstance(v, str))
    return "sha256:" + hashlib.sha256("\0".join(texts).encode("utf-8")).hexdigest()


@functools.lru_cache(maxsize=1)
def git_commit() -> str | None:
    """The commit of the dokimi checkout this code runs from; ``None`` for an installed wheel."""
    from dokimi import process

    try:
        done = process.git("rev-parse", "HEAD", cwd=Path(__file__).resolve().parent, timeout=5)
    except OSError:
        return None
    sha = done.stdout.strip() if done.ok else ""
    return sha or None


def provenance(*, artifact_digest: str | None, answerer_model: str, judge_model: str,
               context_framing: str) -> dict:
    """The ``provenance`` block of a novelty JSON output."""
    return {
        "dokimi_version": __version__,
        "git_commit": git_commit(),
        "artifact_digest": artifact_digest,
        "prompts_sha256": prompts_sha256(),
        "answerer_model": answerer_model,
        "judge_model": judge_model,
        "context_framing": context_framing,
        **vendor_record(answerer_model, judge_model),
    }
