# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Whether litellm refuses one sampling value on one route, before any request is sent.

Kept apart from ``dokimi.llm`` (which it sizes) so the name check there can ask it
without an import cycle.
"""

from __future__ import annotations

from typing import Any, Callable


def _litellm_reason(text: str) -> str:
    """litellm's reason alone: its ``litellm.<Error>: `` prefix and its advice to set a litellm
    global (dokimi never sets it) are dropped."""
    if text.startswith("litellm."):
        text = text.partition(": ")[2]
    reason = text.split(" To drop unsupported params", 1)[0].strip()
    # no final period: a message adds its own, and a refusal clause wraps the reason in parentheses
    return reason.rstrip(".") or "the provider does not take this value"


def refusal_reason(litellm: Any, resolved: str, provider: str, name: str, value: Any) -> str | None:
    """The reason when litellm (passed in; dokimi.llm owns the import) refuses this value on this route.

    litellm wants the provider and the bare id apart, so the provider prefix (and Bedrock's
    ``converse/`` route) is split off and the rest, an ARN or a publisher/model id, is kept whole.
    Any other failure reads as accepted.
    """
    route, separator, bare = resolved.partition("/")
    if not separator:
        route, bare = provider, resolved
    if route == "bedrock":
        bare = bare.removeprefix("converse/")
    try:
        litellm.get_optional_params(model=bare, custom_llm_provider=route or provider,
                                    **{name: value})
    except litellm.UnsupportedParamsError as exc:
        return _litellm_reason(str(exc))
    except Exception:  # noqa: BLE001 - a probe failure must not block a run
        return None
    return None


NAME_REFUSAL = "parameter unsupported"


def refusals_for_route(requested: dict, provider: str, *, cli: Any, load: Callable[[], Any],
                       resolve: Callable[[], str]) -> dict[str, str]:
    """Each requested option the route refuses, with its reason; ``load`` and ``resolve`` run inside the check.

    A CLI backend takes only the names it lists. Anything that stops the check from running (litellm
    will not load, an unknown model) refuses nothing.
    """
    if not requested:
        return {}
    if cli is not None:
        return {name: NAME_REFUSAL for name in requested if name not in cli.supports}
    try:
        litellm = load()
        resolved = resolve()
        supported = litellm.get_supported_openai_params(model=resolved) or []
        if not supported:  # litellm lists nothing for this model: the check cannot run, so nothing is refused
            return {}
    except Exception:  # noqa: BLE001 - an unknown model, or litellm that will not load, does not block the run
        return {}
    refused = {}
    for name, value in requested.items():
        if name not in supported:
            refused[name] = NAME_REFUSAL
        elif reason := refusal_reason(litellm, resolved, provider, name, value):
            refused[name] = reason
    return refused


def refusal_clause(refused: dict[str, str]) -> str:
    """The refused options for an error message, each with litellm's reason when it is a value refusal."""
    return ", ".join(f"{name} ({reason})" if reason != NAME_REFUSAL else name
                     for name, reason in refused.items())
