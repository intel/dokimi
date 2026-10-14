# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""What the judge is shown about an arm's treatment material.

The judge's ``context`` slot used to be filled from the arm record's
``context_chunks``, whose ``snippet`` field is a 200-char preview kept for the
report. A skill arm therefore injected 12,000 characters into the answering
model and showed the judge 200 of them, and an agentic arm — whose
``context_chunks`` is empty by construction — showed it none at all.

That is not a cosmetic truncation. A judge that cannot see the treatment
material cannot distinguish a correctly cited bundled file from an invented one,
so it scores the arm that *uses* its material down for "fabrication" (measured:
17 of 81 skill-arm answers on the perf golden set, -22.5 aggregate on those
questions, enough to invert the arm comparison). Full material in, and the
penalty goes away.

Scope: this is the *material the treatment made available*, which for an agentic
arm is deliberately not the same as the subset the agent chose to read — the
transcript keeps only previews, and availability is the thing the judge cannot
otherwise check. Query-driven treatments (``mcp_agent``) contribute nothing,
because what they would serve is unknown until the agent asks.

The exception is a ``skill-catalog:`` arm, whose material does not fit: the
33-skill intel/skills catalog is ~285k characters against the 20,000 cap, so a
whole-catalog reference showed the judge the alphabetically first two or three
skills whatever the agent loaded. Its reference
(``AgentConfig.judge_reference_for_loaded``) is built after the loop from the
skills the agent loaded — each body given an equal share of the cap — plus
every catalog file list. A view tool serves a skill deterministically, so the
loaded names are enough to rebuild what was read.
"""

import logging
import threading
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

#: Stamped as ``judge_context`` on every judged ``arms.v1`` artifact. Runs before
#: this module were judged on report snippets and carry no stamp; a stamped and an
#: unstamped run used two judge protocols and their scores do not pair
#: (``plugin_delta`` and ``model_compare`` refuse them). Bump it when what the judge
#: is shown changes again.
PROTOCOL = "treatment_material.v1"

#: Per-score ``judge_context_source`` values: the judge saw the arm's stored
#: material, or fell back to the record's 200-char report snippets.
SOURCE_STORED = "stored"
SOURCE_SNIPPET_FALLBACK = "snippet_fallback"

#: Stamped when some of a run's scores saw the stored material and some fell
#: back to snippets. It equals neither protocol, so it pools with nothing.
PROTOCOL_MIXED = PROTOCOL + "+snippet_fallback"


def stamp_for(sources: Iterable[Optional[str]]) -> Optional[str]:
    """The ``judge_context`` stamp a judged run has actually earned.

    The stamp says what the judge was shown, so it has to follow the per-score
    sources rather than the code path that ran. Every score on stored material
    -> :data:`PROTOCOL`. Every score on snippets -> ``None``: that is exactly the
    protocol unstamped runs used before the stamp existed, so leaving it off
    lets the run pool with them, which is correct. A mix -> :data:`PROTOCOL_MIXED`,
    which pools with nothing. Scores without a source (none were judged) do not
    count either way; a run with no counted scores keeps :data:`PROTOCOL`.

    Measured before this existed: a question set without ids, one with
    duplicated ids, and an answers file judged in a new process all produced
    100% ``snippet_fallback`` scores under a ``treatment_material.v1`` stamp.
    """
    seen = {s for s in sources if s in (SOURCE_STORED, SOURCE_SNIPPET_FALLBACK)}
    if seen == {SOURCE_SNIPPET_FALLBACK}:
        return None
    if seen == {SOURCE_STORED, SOURCE_SNIPPET_FALLBACK}:
        return PROTOCOL_MIXED
    return PROTOCOL

#: Cap on the injected part when the caller does not pass the answerer's own cap.
#: Matches ``arm_runner.MAX_CONTEXT_CHARS``; ``ArmRunner`` passes that constant
#: explicitly so the two cannot drift apart.
DEFAULT_INJECTED_CHARS = 15_000

#: Cap on the reference part (manifest, or the skill itself for an agentic arm).
#: A skill body is capped at 12,000 by ``Skill.as_context`` and the manifest at
#: 200 paths (~6,000 chars), so this is a ceiling for pathological input, not a
#: cut that ordinary arms reach. A ``skill-catalog:`` arm does reach it, and
#: splits it among the skills it loaded rather than being cut at the end.
MAX_JUDGE_REFERENCE_CHARS = 20_000

NO_CONTEXT = "(No documentation retrieved)"

_SEP = "\n\n---\n\n"


def join_injected(injected_context: List[Dict[str, Any]]) -> str:
    """The injected material as the answering prompt carries it.

    One helper for both sides, so the answerer and the judge cannot diverge on
    something as small as an empty chunk (which still contributes a separator).
    """
    return _SEP.join(c.get("content", "") for c in injected_context)


def build(
    injected_context: List[Dict[str, Any]],
    judge_reference: str = "",
    *,
    injected_chars: int = DEFAULT_INJECTED_CHARS,
) -> str:
    """Render one arm's treatment material for the judge.

    *injected_context* is the chunk list from the arm's ``AgentConfig`` — full
    ``content``, not report snippets. *judge_reference* is the material the
    answering model did not receive in its prompt (a skill's file manifest, or
    for an agentic skill arm the skill itself).

    The injected part is joined and cut exactly the way the answering prompt is
    (:func:`join_injected`, same ``injected_chars`` limit), so the judge sees
    what the answerer saw — no less, which is the defect this module fixes, and
    no more, since a judge shown text the answerer never received could reward
    citing it. Callers pass an empty *injected_context* for an agentic arm,
    whose answering path does not put injected material in the prompt.

    The reference is the one deliberate exception to "no more": a skill arm's
    file manifest is not in the answering prompt (the agent reaches the files
    through tools, or not at all), but it is the only way the judge can tell a
    cited bundled path from an invented one. For an agentic skill arm the
    reference also carries the skill body, which the agent sees only if it
    calls the view tool, so there the judge may hold text the answer never had
    access to. It is appended after the cut rather
    than competing with the injected material for one budget, so it is never the
    part that gets dropped.
    """
    body = join_injected(injected_context)[:injected_chars]
    if not body.replace(_SEP.strip(), "").strip():
        # Only separators and whitespace (e.g. every chunk empty): no material.
        body = ""
    reference = judge_reference
    if len(reference) > MAX_JUDGE_REFERENCE_CHARS:
        reference = reference[:MAX_JUDGE_REFERENCE_CHARS].rstrip() + "\n…"
    if reference:
        body = f"{body}{_SEP}{reference}" if body else reference
    return body


class JudgeContextStore:
    """Per-run map from ``(question_id, arm_name)`` to the judge's context.

    Kept in memory rather than in the arm record because the record is persisted
    and schema-locked: writing 12,000 characters of skill body into every arm of
    every question would multiply the artifact's size by the question count for
    text that is identical in all of them. Answering and judging happen in the
    same process (``commands/arms.py``), so an in-memory handoff is enough;
    :meth:`get` reports a miss so that judging records loaded from disk degrades
    visibly instead of silently scoring against a 200-char preview.
    """

    def __init__(self) -> None:
        self._ctx: Dict[Tuple[str, str], str] = {}
        #: Keys stored twice with different contexts. A question set without
        #: ids makes every question ``unknown_q``, and duplicated ids do the
        #: same; either way one key would otherwise hold whichever question was
        #: answered last, and every other question would be judged against it.
        self._ambiguous: Set[Tuple[str, str]] = set()
        self._lock = threading.Lock()
        self._warned = False

    def put(self, question_id: str, arm_name: str, context: str) -> None:
        key = (question_id, arm_name)
        with self._lock:
            if key in self._ambiguous:
                return
            previous = self._ctx.get(key)
            if previous is not None and previous != context:
                # Refuse rather than guess: the record's own snippets cannot mix
                # questions, a shared key can.
                self._ambiguous.add(key)
                del self._ctx[key]
                logger.warning(
                    "Judge context key %s/%s was stored for more than one question "
                    "(question ids missing or duplicated) — those answers are judged "
                    "against their own report snippets instead.",
                    question_id, arm_name,
                )
                return
            self._ctx[key] = context

    def get(self, question_id: str, arm_name: str) -> Optional[str]:
        """Return the stored context, or ``None`` when this run did not store it
        or stored it ambiguously."""
        with self._lock:
            key = (question_id, arm_name)
            hit = self._ctx.get(key)
            if hit is None and key not in self._ambiguous and not self._warned:
                self._warned = True
                logger.warning(
                    "No stored judge context for %s/%s — judging records that were not "
                    "answered in this process falls back to the 200-char report "
                    "snippets, which under-scores arms that cite their own material.",
                    question_id, arm_name,
                )
        return hit

    def resolve(self, question_id: str, arm_name: str, arm: Dict[str, Any]) -> str:
        """The context string to hand the judge for one arm's answer."""
        return self.resolve_with_source(question_id, arm_name, arm)[0]

    def resolve_with_source(
        self, question_id: str, arm_name: str, arm: Dict[str, Any]
    ) -> Tuple[str, str]:
        """The judge's context for one arm's answer, and where it came from.

        Prefers what the arm was actually given (:data:`SOURCE_STORED`). Falls
        back to the record's report snippets — truncated to 200 chars each, and
        empty for an agentic arm — which is what every run before this store did
        (:data:`SOURCE_SNIPPET_FALLBACK`). The source is returned so each score
        can record it: one run can mix both, and the warning fires only once.
        """
        stored = self.get(question_id, arm_name)
        if stored is not None:
            return stored or NO_CONTEXT, SOURCE_STORED
        snippets = "\n".join(c.get("snippet", "") for c in arm.get("context_chunks", []))
        return snippets or NO_CONTEXT, SOURCE_SNIPPET_FALLBACK
