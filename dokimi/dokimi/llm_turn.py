# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The read of one chat response: its first choice's text, tool calls and finish reason.

:func:`parse_turn` is the reader of the adapter and the tool-choice probe;
:mod:`dokimi.eval.agent_runner` still has its own. The adapter (:mod:`dokimi.llm`) reads
leniently; the tool-choice probe (:mod:`dokimi.commands.tools`) reads strictly. Both read the
same fields the same way. No provider SDK is imported here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional, Tuple

# finish_reason values that mean "cut at the output ceiling": litellm normalizes to
# "length"; some provider passthroughs keep Anthropic's "max_tokens".
_TRUNCATED = frozenset({"length", "max_tokens"})


@dataclass(frozen=True)
class ToolCall:
    """One tool call the model asked for; ``arguments`` is the raw JSON string."""

    id: Optional[str]
    name: Optional[str]
    arguments: str = "{}"


def _tool_calls(message: Any) -> Tuple[ToolCall, ...]:
    """Normalize a litellm message's tool calls (objects or dicts)."""
    calls = getattr(message, "tool_calls", None)
    out = []
    for tc in calls or ():
        if isinstance(tc, dict):
            fn = tc.get("function")
            if not isinstance(fn, dict):
                fn = {}
            out.append(ToolCall(tc.get("id"), fn.get("name"), fn.get("arguments") or "{}"))
        else:
            fn = getattr(tc, "function", None)
            out.append(
                ToolCall(
                    getattr(tc, "id", None),
                    getattr(fn, "name", None),
                    getattr(fn, "arguments", None) or "{}",
                )
            )
    return tuple(out)


@dataclass(frozen=True)
class ChatTurn:
    """The first choice of a chat response: its text, its tool calls, how it ended."""

    text: Optional[str]
    tool_calls: Tuple[ToolCall, ...] = ()
    finish_reason: Optional[str] = None

    @property
    def truncated(self) -> bool:
        """Whether the choice was cut at the output ceiling."""
        return self.finish_reason in _TRUNCATED


def parse_turn(resp: Any, *, strict: bool = False) -> ChatTurn:
    """Read the first choice of a litellm response *resp*.

    Lenient (the default, the adapter's reading): a response with no choices, which some
    routes bill as a 200, reads as an empty turn; its callers price it. Strict: no choice
    raises instead, as a caller that needs an answer wants (``IndexError`` for an empty
    list, ``TypeError`` for ``None``, ``AttributeError`` for a choice with no ``message``).
    """
    if strict:
        choice = resp.choices[0]
        message = choice.message
    else:
        choices = getattr(resp, "choices", None) or ()
        choice = choices[0] if choices else None
        message = getattr(choice, "message", None)
    return ChatTurn(
        text=getattr(message, "content", None),
        tool_calls=_tool_calls(message),
        finish_reason=getattr(choice, "finish_reason", None),
    )
