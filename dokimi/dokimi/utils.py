# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

import os
from typing import Tuple, Dict, Any


def parse_frontmatter(text: str) -> "Tuple[Dict[str, Any], str]":
    """Split a Markdown document into (frontmatter dict, body).

    Supports a leading YAML frontmatter block delimited by ``---`` lines, the
    same convention used by Claude Code skill files and most static-site
    generators. When no frontmatter is present, returns ``({}, text)``.

    Args:
        text: Raw file contents.

    Returns:
        Tuple of (metadata dict, body string with frontmatter stripped).
    """
    import yaml

    if not text:
        return {}, ""

    stripped = text.lstrip("﻿")  # tolerate a UTF-8 BOM
    if not stripped.startswith("---"):
        return {}, stripped

    lines = stripped.splitlines()
    # First line is the opening '---'; find the closing fence.
    for idx in range(1, len(lines)):
        if lines[idx].strip() == "---":
            raw_meta = "\n".join(lines[1:idx])
            body = "\n".join(lines[idx + 1:]).lstrip("\n")
            try:
                meta = yaml.safe_load(raw_meta) or {}
            except yaml.YAMLError:
                meta = {}
            if not isinstance(meta, dict):
                meta = {}
            return meta, body

    # No closing fence — treat the whole thing as body.
    return {}, text


def digest_text(text: str) -> str:
    """Content-digest a prompt or other text blob.

    Uses the ``sha256:<hexdigest>`` prefix convention the plugin-set and subject
    digests already use, so provenance fields read the same way across artifacts.
    The digest itself is the full 64-char hex: the plugin-set digest is truncated
    to 32, so the two are comparable in shape but never equal for the same input.

    Args:
        text: The text to digest.

    Returns:
        ``"sha256:"`` followed by the hex digest of the UTF-8 encoding.
    """
    import hashlib

    return "sha256:" + hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def normalize_model_ref(provider: str, model: str) -> str:
    """Normalize provider and model names for comparison."""
    return f"{(provider or '').strip().lower()}/{(model or '').strip().lower()}"


def _read_key(value: str) -> str:
    """Resolve API key value: supports 'file:~/path' references."""
    if not value:
        return value
    if value.startswith("file:"):
        path = os.path.expanduser(value[5:])
        try:
            return open(path, encoding="utf-8").read().strip()
        except Exception:
            return value
    return value


def bedrock_litellm_model(model: str) -> str:
    """The litellm model string ``get_llm`` sends to Bedrock for ``model``."""
    # A Bedrock id always has a dot ("anthropic.", "us.anthropic.", "amazon.").
    # Only a bare name such as the default judge model gets mapped.
    if not model.startswith("arn:") and "." not in model:
        model = os.environ.get("ANTHROPIC_DEFAULT_SONNET_MODEL", model)
    # Bedrock's openai.* namespace 404s on the plain invoke route; it only
    # answers via Converse (mirrors the fix in llm.py's _build_litellm_model).
    prefix = "bedrock/converse/" if "openai." in model else "bedrock/"
    return f"{prefix}{model}"


def get_llm(provider: str, model: str, api_key: str = None,
            temperature: float = None, seed: int = None):
    """Return a minimal chat wrapper with .invoke(prompt) -> object(content=...)."""
    from litellm import completion

    class _Resp:
        def __init__(self, content: str, usage=None):
            self.content = content
            self.usage = usage   # UsageRecord | None — lets the judge track cost

    class _LLM:
        def __init__(self, litellm_model: str, key: str = None, default_max_tokens: int = None,
                     temperature: float = None, seed: int = None):
            self.litellm_model = litellm_model
            self.key = key
            self.default_max_tokens = default_max_tokens
            self.temperature = temperature
            self.seed = seed

        def invoke(self, prompt: str, draw: int = 0, cache_if=None):
            import time as _time
            from dokimi import caching
            from dokimi.llm import _extract_usage, _is_retryable, _sampling_kwargs
            kwargs = _sampling_kwargs(
                max_tokens=self.default_max_tokens or None,
                temperature=self.temperature, seed=self.seed,
            )
            messages = [{"role": "user", "content": prompt}]
            # The judge reaches Bedrock, Vertex and OpenRouter through here, not
            # through llm_call, so this path needs the response cache too.
            # temperature/seed enter the key only when set.
            key = caching.response_cache_key(
                litellm_model=self.litellm_model, messages=messages,
                extra=caching.draw_extra(
                    {"max_tokens": self.default_max_tokens,
                     **_sampling_kwargs(temperature=self.temperature, seed=self.seed)},
                    draw,
                ),
            )
            hit = caching.load_response(key)
            if hit is not None and caching.keeps(cache_if, hit[0]):
                return _Resp(hit[0], usage=hit[1])
            delay = 2.0
            for attempt in range(4):
                t0 = _time.time()
                try:
                    res = completion(
                        model=self.litellm_model, messages=messages, api_key=self.key, **kwargs,
                    )
                    break
                except Exception as exc:
                    # Throttling is routine at judge concurrency; a lost pair is not.
                    if attempt == 3 or not _is_retryable(exc):
                        raise
                    _time.sleep(delay)
                    delay *= 2
            latency = _time.time() - t0
            # Pass the provider-prefixed litellm_model so cost resolves for
            # Vertex/Bedrock (their response.model is bare → null cost otherwise).
            usage = _extract_usage(res, self.litellm_model, "litellm",
                                   latency_sec=latency, litellm_model=self.litellm_model)
            text = res.choices[0].message.content
            if caching.keeps(cache_if, text):
                caching.store_response(key, text, usage)
            return _Resp(text, usage=usage)

    api_key = _read_key(api_key)

    if provider == "openrouter":
        key = api_key or _read_key(os.environ.get("OPENROUTER_API_KEY", ""))
        litellm_model = model if model.startswith("openrouter/") else f"openrouter/{model}"
        return _LLM(litellm_model=litellm_model, key=key, default_max_tokens=2000,
                    temperature=temperature, seed=seed)

    if provider in ("google", "gemini", "google-vertex"):
        # Route Gemini via Vertex AI adapter
        litellm_model = model if model.startswith("vertex_ai/") else f"vertex_ai/{model}"
        key = api_key or _read_key(os.environ.get("GEMINI_API_KEY", ""))
        return _LLM(litellm_model=litellm_model, key=key,
                    temperature=temperature, seed=seed)

    if provider == "amazon-bedrock":
        bearer = _read_key(os.environ.get("AWS_BEARER_TOKEN_BEDROCK", ""))
        return _LLM(litellm_model=bedrock_litellm_model(model), key=bearer,
                    temperature=temperature, seed=seed)

    if provider == "openai":
        key = api_key or _read_key(os.environ.get("OPENAI_API_KEY", ""))
        litellm_model = model
        return _LLM(litellm_model=litellm_model, key=key,
                    temperature=temperature, seed=seed)

    if provider == "anthropic":
        key = api_key or _read_key(os.environ.get("ANTHROPIC_API_KEY", ""))
        litellm_model = model if model.startswith("anthropic/") else f"anthropic/{model}"
        return _LLM(litellm_model=litellm_model, key=key,
                    temperature=temperature, seed=seed)

    key = api_key
    litellm_model = model if "/" in model else f"{provider}/{model}"
    return _LLM(litellm_model=litellm_model, key=key,
                temperature=temperature, seed=seed)


def question_id(q: Dict[str, Any]) -> Any:
    """The question's ``id`` or ``question_id``; ``None`` when neither is set.

    ``0`` is an id (``questions.v1`` allows integer ids), so ``q.get("id") or ...``
    — which reads it as missing — is not a substitute.
    """
    for key in ("id", "question_id"):
        if q.get(key) not in (None, ""):
            return q[key]
    return None

