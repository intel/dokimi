# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

import json
import os
import re
from pathlib import Path
from typing import Tuple, Dict, Any

from dokimi.defaults import bedrock_profile
from dokimi.exit_codes import EXIT_USAGE


def parse_frontmatter(text: str) -> "Tuple[Dict[str, Any], str]":
    """Split a Markdown document into (frontmatter dict, body).

    Supports a leading YAML frontmatter block delimited by ``---`` lines, the
    same convention used by Claude Code skill files and most static-site
    generators. When no frontmatter is present, returns ``({}, text)``. Frontmatter
    that is not valid YAML also returns ``{}``; use :func:`parse_frontmatter_strict`
    to see that error.

    Args:
        text: Raw file contents.

    Returns:
        Tuple of (metadata dict, body string with frontmatter stripped).
    """
    return _split_frontmatter(text, strict=False)


def parse_frontmatter_strict(text: str) -> "Tuple[Dict[str, Any], str]":
    """Like :func:`parse_frontmatter`, but frontmatter that is not valid YAML raises.

    For a validator that must name the YAML error rather than read the document
    as having no metadata. The lenient :func:`parse_frontmatter` is unchanged for
    every other caller.

    Raises:
        yaml.YAMLError: The frontmatter block is not valid YAML.
    """
    return _split_frontmatter(text, strict=True)


def _split_frontmatter(text: str, *, strict: bool) -> "Tuple[Dict[str, Any], str]":
    import yaml

    if not text:
        return {}, ""

    stripped = text.lstrip("﻿")  # tolerate a UTF-8 BOM
    lines = stripped.splitlines()
    # Same rule as the closing fence: a line that is exactly ``---``. A thematic
    # break (``-----``) or a ``--- title`` line opens nothing.
    if not lines or lines[0].rstrip() != "---":
        return {}, stripped

    # First line is the opening '---'; find the closing fence.
    for idx in range(1, len(lines)):
        if lines[idx].strip() == "---":
            raw_meta = "\n".join(lines[1:idx])
            body = "\n".join(lines[idx + 1:]).lstrip("\n")
            try:
                meta = yaml.safe_load(raw_meta) or {}
            except yaml.YAMLError:
                if strict:
                    raise
                meta = {}
            if not isinstance(meta, dict):
                meta = {}
            return meta, body

    # No closing fence — treat the whole thing as body.
    return {}, stripped


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

    return "sha256:" + hashlib.sha256((text or "").encode("utf-8", errors="surrogatepass")).hexdigest()


def retry_writable(func: Any, path: str, _exc: Any) -> None:
    """``shutil.rmtree`` ``onerror`` hook: set the write bit on ``path`` and retry once.

    Windows will not delete a read-only file, and git leaves its object files
    read-only. A symlink is left alone, since chmod would follow it out of the
    tree. A retry that still fails is ignored; the caller checks what is left.
    Only the delete calls are retried: on POSIX ``rmtree`` also reports a failed
    ``os.open`` or ``os.scandir`` (an unreadable dir), which ``func(path)`` would
    call with the wrong arguments and raise ``TypeError``.
    """
    import stat

    try:
        mode = os.lstat(path).st_mode
        if not stat.S_ISLNK(mode):
            os.chmod(path, stat.S_IMODE(mode) | stat.S_IWRITE)
            if func in (os.unlink, os.remove, os.rmdir):
                func(path)
    except OSError:
        pass


# Bedrock cross-region inference profiles: the same model served from another region. They are dropped
# from any provider's name, not only Bedrock's: no non-Bedrock id is known to start with one of them.
_REGION_PROFILES = ("us-gov.", "global.", "apac.", "us.", "eu.", "jp.", "au.", "ca.")


# Leading segments that name a route (provider or gateway: ``anthropic/``, ``openrouter/``), not a model; any other
# segment (an HF org such as ``meta-llama/``) is part of the id. Holds ``llm.PROVIDERS`` (test-pinned) + litellm routes.
_ROUTES = frozenset({
    "openai", "openai-codex", "anthropic", "google", "gemini", "google-vertex", "openrouter", "amazon-bedrock",
    "bedrock", "converse", "vertex_ai", "azure", "azure_ai", "models", "groq", "ollama", "mistral", "deepseek",
    "together_ai", "fireworks_ai", "xai", "cohere", "huggingface",
})


# Vendors whose Bedrock ids start with ``vendor.`` (``anthropic.claude-...``, ``zai.glm-4.7``, ``xai.grok-4.6``).
_VENDORS = frozenset({
    "ai21", "amazon", "anthropic", "cohere", "deepseek", "google", "luma", "meta", "minimax", "mistral",
    "moonshotai", "nvidia", "openai", "qwen", "stability", "twelvelabs", "writer", "xai", "zai",
})


def _model_core(model: str) -> tuple[str, str | None]:
    """A model name reduced to ``(id, snapshot date or None)``. A snapshot is ``-YYYYMMDD`` or OpenAI's ``-YYYY-MM-DD``.

    The route goes (``openrouter/anthropic/``, ``bedrock/``, ``vertex_ai/``, an ARN's
    ``inference-profile/``), and so do the case, the region profile (``eu.``) and a Bedrock
    revision (``-v1:0`` or ``-1:0``). Vertex's ``@20250929`` is the snapshot ``-20250929``. The organization of an
    ``org/name`` id stays, so a re-upload under another organization is another model.
    """
    name = (model or "").strip().lower()
    for marker in ("inference-profile/", "foundation-model/"):  # the tail of a Bedrock ARN
        if marker in name:
            name = name.rsplit(marker, 1)[-1]
    parts = name.split("/")
    while len(parts) > 1 and parts[0] in _ROUTES:
        hub = parts.pop(0) == "huggingface"
        if hub:  # a Hub id: the route goes, its organization stays, even behind another route
            break
    name = "/".join(parts)
    name = re.sub(r"-v?\d+:\d+$", "", name)
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
    ids match only on the same date, and a bare family name (``haiku``) is not a full id.

    The provider is not part of the identity: the same weights on Bedrock and on the direct
    API are still one model for the self-judge warning and for a baseline's model check.
    """
    (base_a, date_a), (base_b, date_b) = model_identity(a), model_identity(b)
    if base_a is None or base_b is None or (date_a and date_b and date_a != date_b):
        return False
    return base_a == base_b


def _read_key(value: str) -> str:
    """Resolve API key value: supports 'file:~/path' references."""
    if not value:
        return value
    if value.startswith("file:"):
        path = os.path.expanduser(value[5:])
        try:
            with open(path, encoding="utf-8") as fh:
                return fh.read().strip()
        except Exception:
            return value
    return value


def bedrock_litellm_model(model: str) -> str:
    """The litellm model string ``get_llm`` sends to Bedrock for ``model``."""
    # A Bedrock id always has a dot ("anthropic.", "us.anthropic.", "amazon.").
    # Only a bare name gets mapped: a known one (such as the default judge) to its
    # inference profile, any other to the box's default Sonnet.
    if not model.startswith("arn:") and "." not in model:
        model = bedrock_profile(model) or os.environ.get("ANTHROPIC_DEFAULT_SONNET_MODEL", model)
    # Bedrock's openai.* namespace 404s on the plain invoke route; it only
    # answers via Converse (mirrors llm.resolve_model).
    prefix = "bedrock/converse/" if "openai." in model else "bedrock/"
    return f"{prefix}{model}"


def get_llm(provider: str, model: str, api_key: str | None = None,
            temperature: float | None = None, seed: int | None = None) -> Any:
    """A chat wrapper, ``.invoke(prompt, draw=, cache_if=)`` -> ``.content``/``.usage``.

    A delegate to :class:`dokimi.llm.LLM` that keeps this path's own provider
    table (it differs from :func:`dokimi.llm.resolve_model`: a bare Bedrock name
    falls back to ``$ANTHROPIC_DEFAULT_SONNET_MODEL``, OpenRouter gets
    ``max_tokens=2000``, Bedrock reads only its bearer token) and its cache keys,
    until the judge, generator and validator move to ``LLM``.
    """
    from dokimi import llm_cli
    from dokimi.llm import LLM, _GetLLMCompat, _Invoker

    if llm_cli.backend(provider) is not None:  # a logged-in CLI: no route, no key
        return _Invoker(LLM(model, provider, temperature=temperature, seed=seed))
    api_key = _read_key(api_key) if api_key else api_key
    max_tokens: int | None = None
    key: str | None
    if provider == "openrouter":
        key = api_key or _read_key(os.environ.get("OPENROUTER_API_KEY", ""))
        route = model if model.startswith("openrouter/") else f"openrouter/{model}"
        max_tokens = 2000
    elif provider in ("google", "gemini", "google-vertex"):
        # Route Gemini via Vertex AI adapter
        route = model if model.startswith("vertex_ai/") else f"vertex_ai/{model}"
        key = api_key or _read_key(os.environ.get("GEMINI_API_KEY", ""))
    elif provider == "amazon-bedrock":
        route = bedrock_litellm_model(model)
        key = _read_key(os.environ.get("AWS_BEARER_TOKEN_BEDROCK", ""))
    elif provider == "openai":
        route = model
        key = api_key or _read_key(os.environ.get("OPENAI_API_KEY", ""))
    elif provider == "anthropic":
        route = model if model.startswith("anthropic/") else f"anthropic/{model}"
        key = api_key or _read_key(os.environ.get("ANTHROPIC_API_KEY", ""))
    else:
        route = model if "/" in model else f"{provider}/{model}"
        key = api_key
    return _Invoker(_GetLLMCompat(model, provider, max_tokens=max_tokens,
                                  temperature=temperature, seed=seed, route=route, key=key))


def question_id(q: Dict[str, Any]) -> Any:
    """The question's ``id`` or ``question_id``; ``None`` when neither is set.

    ``0`` is an id (``questions.v1`` allows integer ids), so ``q.get("id") or ...``
    — which reads it as missing — is not a substitute.
    """
    for key in ("id", "question_id"):
        if q.get(key) not in (None, ""):
            return q[key]
    return None


class InputError(Exception):
    """A user-supplied input file that cannot be used; ``cli.main`` prints it and exits 2."""


def read_json_input(path: str | os.PathLike[str], *, flag: str) -> Any:
    """The parsed JSON at ``path``, or raises :class:`InputError` naming ``flag`` and the file."""
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError) as exc:  # JSONDecodeError and bad UTF-8 are ValueErrors
        reason = exc.strerror if isinstance(exc, OSError) and exc.strerror else exc
        raise InputError(f"{flag} {path}: {reason}") from exc


def write_text_or_exit(path: Path, text: str, code: int = EXIT_USAGE) -> None:
    """Write ``text`` to ``path`` (creating parents), or exit ``code`` naming ``path`` when it cannot be written."""
    import sys
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    except OSError as exc:
        print(f"Error: cannot write {path}: {exc}", file=sys.stderr)
        sys.exit(code)
