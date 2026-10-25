# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Multi-turn session measurement for one arm: history kept, usage per turn.

:class:`~dokimi.eval.arm_runner.ArmRunner` answers each question in one call.
With ``session_turns`` set, a question is asked as its opening question followed
by its ``follow_ups``; the conversation so far is sent with every turn, and the
token usage (input, output, cache read and write, cost) of each turn is recorded.
That is what a per-turn cost or style claim needs: a single-shot answer cannot
show what a skill or instruction costs once the context has grown.

Measurement only. Sessions are not judged: the judge scores one answer against one
question, and a session's last answer is not that.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Sequence

from dokimi import caching
from dokimi.llm import LLM
from dokimi.metrics.usage import UsageRecord

logger = logging.getLogger(__name__)

#: Appended to an arm's name for its reminder variant (the same arm, with the
#: reminder text added to every user turn).
REMINDER_ARM_SUFFIX = "+reminder"

#: Question field holding the follow-up user turns, after the question itself.
FOLLOW_UP_FIELD = "follow_ups"


def session_turns_of(question: Dict[str, Any], question_text: str,
                     n_turns: int, label: str) -> List[str]:
    """The user turns of one question's session: its text, then ``n_turns - 1`` follow-ups.

    Raises ``ValueError`` rather than running a shorter session, so every record of a
    run has the same number of turns.
    """
    follow_ups = question.get(FOLLOW_UP_FIELD) or []
    if not isinstance(follow_ups, list) or not all(
            isinstance(t, str) and t.strip() for t in follow_ups):
        raise ValueError(f"question {label}: '{FOLLOW_UP_FIELD}' must be a list of non-blank strings")
    if len(follow_ups) < n_turns - 1:
        raise ValueError(
            f"question {label} has {len(follow_ups)} '{FOLLOW_UP_FIELD}' entr"
            f"{'y' if len(follow_ups) == 1 else 'ies'}; --session-turns {n_turns} needs {n_turns - 1}"
        )
    return [question_text, *follow_ups[: n_turns - 1]]


def _with_reminder(text: str, reminder: Optional[str]) -> str:
    if reminder is None:
        return text
    return f"{text}\n\n<system-reminder>{reminder}</system-reminder>"


def run_session(
    *,
    model: str,
    provider: str,
    opening: str,
    follow_ups: Sequence[str],
    system: Optional[str] = None,
    api_key: Optional[str] = None,
    max_tokens: Optional[int] = None,
    temperature: Optional[float] = None,
    seed: Optional[int] = None,
    reminder: Optional[str] = None,
    cache_prefix: Optional[str] = None,
) -> Dict[str, Any]:
    """Answer a conversation turn by turn, sending the full history each time.

    ``opening`` is the first user message (the prompt the single-turn path would
    send, context included); each ``follow_ups`` entry is a later user message.
    ``reminder``, when set, is appended to every user message. ``cache_prefix``, when
    set, is the static start of the first message (``opening`` is then the rest), sent
    as its own block with a cache breakpoint as the single-turn path sends it.

    Returns ``answer`` (the last assistant text, ``None`` if no turn completed),
    ``turns`` (per turn: ``user_chars``, ``answer``, ``finish_reason``,
    ``truncated`` and ``metrics``, the :meth:`UsageRecord.as_metrics_dict` of that
    call), ``stopped_reason`` (``completed``, ``empty_reply`` or ``error``),
    ``error`` (``None`` unless a turn raised), and ``usage`` (the sum over turns).
    A turn that raises ends the session; the completed turns are kept.
    """
    llm = LLM(model=model, provider=provider, api_key=api_key, max_tokens=max_tokens,
              temperature=temperature, seed=seed)
    messages: List[Dict[str, Any]] = []
    if system:
        messages.append({"role": "system", "content": system})

    total = UsageRecord(model=model, provider=provider, n_calls=0)
    turns: List[Dict[str, Any]] = []
    answer: Optional[str] = None
    stopped_reason = "completed"
    error: Optional[str] = None

    for i, text in enumerate([opening, *follow_ups]):
        content = _with_reminder(text, reminder)
        user_chars = len(content)
        if i == 0 and cache_prefix:
            built, _ = caching.build_messages(content, cache_prefix=cache_prefix,
                                              model=model, provider=provider)
            messages.append(built[-1])
            user_chars += len(cache_prefix)
        else:
            messages.append({"role": "user", "content": content})
        try:
            # A copy: the history grows after this call, and no adapter may see it grow.
            reply = llm.chat(list(messages))
        except Exception as exc:
            logger.exception("Session turn %d failed", i)
            error = f"turn {i}: {exc}"
            stopped_reason = "error"
            break
        text_out = reply.text or ""
        messages.append({"role": "assistant", "content": text_out})
        total = total + reply.usage
        answer = text_out
        turns.append({
            "turn": i,
            "user_chars": user_chars,
            "answer": text_out,
            "finish_reason": reply.finish_reason,
            "truncated": reply.truncated,
            "metrics": reply.usage.as_metrics_dict(answer_chars=len(text_out)),
        })
        if not text_out.strip():
            # An empty assistant message cannot be sent back as history, and a
            # provider rejects it; the rest of the session is not asked.
            stopped_reason = "empty_reply"
            break

    return {
        "answer": answer if turns else None,
        "turns": turns,
        "stopped_reason": stopped_reason,
        "error": error,
        "usage": total,
    }
