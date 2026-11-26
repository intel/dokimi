# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Model identity: whether two model names are one model, and the evaluator-independence warning.

Kept apart from dokimi.utils, which re-exports these names.
"""

from __future__ import annotations

import re

from dokimi import llm_cli


# Bedrock cross-region inference profiles: the same model served from another region. They are dropped
# from any provider's name, not only Bedrock's: no non-Bedrock id is known to start with one of them.
_REGION_PROFILES = ("us-gov.", "global.", "apac.", "us.", "eu.", "jp.", "au.", "ca.")


# Leading segments that name a route (provider or gateway: ``anthropic/``, ``openrouter/``), not a model; any other
# segment (an HF org such as ``meta-llama/``) is part of the id. Holds ``llm.ANSWER_PROVIDERS`` (test-pinned) + litellm routes.
_ROUTES = frozenset({
    "openai", "openai-codex", "anthropic", "google", "gemini", "google-vertex", "openrouter", "amazon-bedrock",
    "bedrock", "converse", "vertex_ai", "azure", "azure_ai", "models", "groq", "ollama", "mistral", "deepseek",
    "together_ai", "fireworks_ai", "xai", "cohere", "huggingface", "claude-cli", "codex-cli", "copilot-cli",
})


# Vendors whose Bedrock ids start with ``vendor.`` (``anthropic.claude-...``, ``zai.glm-4.7``, ``xai.grok-4.6``).
_VENDORS = frozenset({
    "ai21", "amazon", "anthropic", "cohere", "deepseek", "google", "luma", "meta", "minimax", "mistral",
    "moonshotai", "nvidia", "openai", "qwen", "stability", "twelvelabs", "writer", "xai", "zai",
})
_VENDOR_SLUGS = {"z-ai": "zai", "x-ai": "xai", "mistralai": "mistral"}  # OpenRouter's spelling of a vendor
_BEDROCK_ARN = re.compile(r"(?:^|/)arn:aws[a-z-]*:bedrock:[a-z0-9-]*:\d*:(?:inference-profile|foundation-model)/")


def _routed_parts(name: str) -> tuple[list[str], bool]:
    """Drop transport prefixes, keeping a Hugging Face organization and any model vendor."""
    arn = _BEDROCK_ARN.search(name)
    if arn:
        name = name[arn.end():]
    parts = name.split("/")
    while len(parts) > 1 and parts[0] in _ROUTES:
        route = parts.pop(0)
        if route == "huggingface":
            return parts, True
        if route == "openrouter" and len(parts) == 2 and _VENDOR_SLUGS.get(parts[0], parts[0]) in _VENDORS:
            return parts, False
    return parts, False


def _model_core(model: str) -> tuple[str, str | None]:
    """A model name reduced to ``(id, snapshot date or None)``. A snapshot is ``-YYYYMMDD`` or OpenAI's ``-YYYY-MM-DD``.

    The route goes (``openrouter/anthropic/``, ``bedrock/``, ``vertex_ai/``, an ARN's
    ``inference-profile/``), and so do the case, the region profile (``eu.``) and a Bedrock
    revision (``-v1:0`` or ``-1:0``). Vertex's ``@20250929`` is the snapshot ``-20250929``. The organization of an
    ``org/name`` id stays, so a re-upload under another organization is another model; a known vendor
    (``qwen/``, ``z-ai/``) goes, as its Bedrock ``vendor.`` prefix does, unless a ``huggingface/`` route precedes it.
    """
    # sonnet[1m] is the same model with a larger window: the 1M context form goes, and no other bracket
    # (qwen3[8b] and qwen3[32b] are two models)
    name = re.sub(r"\[1m\]$", "", (model or "").strip().lower())
    parts, hub = _routed_parts(name)
    if not hub and len(parts) > 1 and _VENDOR_SLUGS.get(parts[0], parts[0]) in _VENDORS:
        parts.pop(0)  # OpenRouter's ``qwen/X`` is Bedrock's ``qwen.X``: the vendor, not an organization
    name = "/".join(parts)
    name = re.sub(r"(?<!claude)-v?\d+:\d+$", "", name)  # not Claude 2's own version: claude-v2:1
    for prefix in _REGION_PROFILES:
        if name.startswith(prefix):
            name = name[len(prefix):]
            break
    name = re.sub(r"@(?=\d{8}$)", "-", name)
    snapshot = re.search(r"-(\d{8}|\d{4}-\d{2}-\d{2})$", name)
    return (name[:snapshot.start()], snapshot.group(1).replace("-", "")) if snapshot else (name, None)


def model_identity(model: str) -> tuple[str | None, str | None]:
    """``(id, snapshot date or None)`` of a model name with its route, region profile and vendor prefix dropped;
    the id is ``None`` for a name with no id. Two names with equal identities are one model and one snapshot."""
    base, date = _model_core(model)
    if not base:
        return None, date
    # a known vendor before the first dot (``anthropic.``, ``openai.``, as in Bedrock ids) is dropped; any other
    # dotted name is part of the id (``alice.model``), and so is a dot after a digit or a dash (``gpt-4.1``)
    head, dot, rest = base.partition(".")
    ident = rest if dot and head in _VENDORS else base
    return (ident if re.search(r"[a-z0-9]", ident) else None), date  # separators alone name no model


def same_model(a: str, b: str) -> bool:
    """Whether two model names are one model.

    The same id matches, and so does a dated snapshot and its undated alias (the alias runs
    some snapshot of it), or the same id behind another route or region profile. Two dated
    ids match only on the same date. A Claude CLI alias (``sonnet``, ``opus[1m]``) matches a
    full id of its family, since the CLI runs the alias as one of them (which one is not known here).

    The provider is not part of the identity: the same weights on Bedrock and on the direct
    API are still one model for the self-judge warning and for a baseline's model check.
    """
    (base_a, date_a), (base_b, date_b) = model_identity(a), model_identity(b)
    if base_a is None or base_b is None:
        return False
    # The alias rule applies only when neither side keeps an organization: ``myorg/sonnet`` is not the CLI's sonnet.
    if "/" not in base_a and "/" not in base_b and (llm_cli.is_alias(a) or llm_cli.is_alias(b)):
        family_a = llm_cli.family(a)
        return family_a is not None and family_a == llm_cli.family(b)
    if date_a and date_b and date_a != date_b:
        return False
    return base_a == base_b


def same_evaluator(answer_model: str, judge_model: str) -> bool:
    """Whether the answer and judge are one model, for the independence warning and its stored flag."""
    return same_model(answer_model, judge_model)


def warn_judge_independence(
    answer_provider: str,
    answer_model: str,
    judge_provider: str,
    judge_model: str,
    context: str = "run",
    roles: tuple[str, str] = ("answer", "judge"),
) -> bool:
    """Emit a non-fatal warning when answer and judge are the same model (on any provider route),
    or a different model of the same vendor family. Never refuses.

    ``roles`` names the two sides in the message, e.g. ``("generator", "reviewer")``.
    Returns whether they are the same model (the stored ``evaluator_independence_warning`` flag).
    """
    import sys

    same = same_evaluator(answer_model, judge_model)
    first, second = roles
    if same:
        print(
            "⚠ Evaluator independence warning "
            f"({context}): {first} model equals {second} model "
            f"[{answer_provider}/{answer_model}] and [{judge_provider}/{judge_model}] — potential self-referential bias.",
            file=sys.stderr,
        )
    elif (family := vendor_of(answer_model)) != UNKNOWN_VENDOR and family == vendor_of(judge_model):
        print(
            "⚠ Evaluator independence warning "
            f"({context}): {first} [{answer_provider}/{answer_model}] and {second} [{judge_provider}/{judge_model}] "
            f"are one {family} family — a judge can favour answers of its own family.",
            file=sys.stderr,
        )
    return same


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
    ("mistral", "mistral"), ("mixtral", "mistral"),
)
UNKNOWN_VENDOR = "unknown"


def vendor_of(model: str | None) -> str:
    """The vendor family a model id names (``us.anthropic.claude-...`` is ``anthropic``), else ``unknown``."""
    parts, _ = _routed_parts((model or "").strip().lower())
    lowered = "/".join(parts)
    return next((vendor for marker, vendor in _VENDOR_MARKERS if marker in lowered), UNKNOWN_VENDOR)
