# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Provider names, aliases and credential environment variables."""

from dokimi import llm_cli


#: Canonical provider names, as the CLI offers them, and the variable each key is read from.
PROVIDERS: dict[str, str] = {
    "openai": "OPENAI_API_KEY",
    "openai-codex": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "amazon-bedrock": "AWS_BEARER_TOKEN_BEDROCK",
    "google-vertex": "GEMINI_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
}

#: ``--provider`` choices: the API providers, then the CLI backends.
ANSWER_PROVIDERS: tuple[str, ...] = (*PROVIDERS, *llm_cli.BACKENDS)

#: ``--provider`` choices of the commands that build the answerer or generators: not openai-codex.
CALLER_CHOICES: tuple[str, ...] = tuple(p for p in ANSWER_PROVIDERS if p != "openai-codex")

#: Providers the answerer, judge and generators take: those, and google, gemini and vertex_ai.
CALLER_PROVIDERS: tuple[str, ...] = (*CALLER_CHOICES, "google", "gemini", "vertex_ai")


def check_caller_provider(provider: str) -> None:
    """Raise ``ValueError`` for a provider the answerer, judge and generators do not take."""
    if provider not in CALLER_PROVIDERS:
        raise ValueError(
            f"Unsupported provider: '{provider}'. "
            "Use 'openai', 'anthropic', 'google', 'openrouter', 'amazon-bedrock' or 'claude-cli'."
        )

#: Other spellings of a canonical provider.
PROVIDER_ALIASES: dict[str, str] = {
    "google": "google-vertex",
    "gemini": "google-vertex",
    "vertex_ai": "google-vertex",
}


def canonical_provider(provider: str) -> str:
    return PROVIDER_ALIASES.get(provider, provider)


def credential_env_var(provider: str) -> str | None:
    """The environment variable holding *provider*'s key; ``None`` off the table (litellm reads it)."""
    return PROVIDERS.get(canonical_provider(provider))
