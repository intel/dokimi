# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Multi-evaluator (LLM Judge Panel) for dokimi.

Design principles:
- Roles are differentiated in BOTH criteria perspective AND weighted aggregate
- LLM outputs only 5 raw scores; Python computes weighted aggregate (reliable)
- reasoning comes FIRST in JSON (chain-of-thought anchors the scores)
- code_quality = 100 when no code present (avoids penalizing text-only answers)
- JSON extracted via regex (robust against leading text from LLM)
- Prompts use __PLACEHOLDER__ substitution (safe from .format() conflicts)
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import statistics
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from dokimi.eval.diagnoser import retrieval_hit
from dokimi.llm import llm_call

logger = logging.getLogger(__name__)

# ── Score fields & weights ────────────────────────────────────────────────────

SCORE_FIELDS = ("correctness", "completeness", "specificity", "code_quality", "actionability")

# Weights per role — Python computes aggregate, not LLM.
#
# `specificity` is weighted here but not in the single judge (see
# `judge.Judge._STANDARD_WEIGHTS`). That is deliberate and follows from the
# wording: in both prompts the dimension now asks whether the identifiers the
# answer names are CORRECT for the library, not whether the library's name
# appears often. Under the old "is it specific to <library>" wording it was a
# vocabulary counter that rewarded the `wrong_tools` degradation for the
# polished names it kept, which is why the single judge zero-weights it; under
# the new wording it is a sub-measure of correctness, which these roles are
# meant to weigh differently. The panel has no degradation measurement behind
# its weights either way — they have never been validated the way the single
# judge's were.
ROLE_WEIGHTS: Dict[str, Dict[str, float]] = {
    "technical_expert":   {"correctness": 2.0, "completeness": 1.0, "specificity": 1.5, "code_quality": 1.0, "actionability": 1.0},
    "developer_advocate": {"correctness": 1.0, "completeness": 1.0, "specificity": 1.0, "code_quality": 1.5, "actionability": 2.0},
    "doc_reviewer":       {"correctness": 1.0, "completeness": 2.0, "specificity": 1.5, "code_quality": 1.0, "actionability": 1.0},
}

# ── Calibration anchors (shared) ──────────────────────────────────────────────

SCORE_ANCHORS = """
Score anchors — interpolate between these points:
  100 = perfect: complete, accurate, specific, immediately usable
   90 = excellent: tiny wording issues or one minor omission, no impact on usability
   80 = good: correct and useful, one noticeable gap or slight imprecision
   70 = acceptable: core answer is right, but missing some context or steps
   60 = partial: answers part of the question well, ignores another part
   50 = mediocre: significant gaps, or correct but too vague to act on
   40 = weak: some useful info but notable inaccuracies or missing key details
   30 = poor: mostly off-target or incorrect, one or two correct points
   20 = very poor: nearly all wrong or irrelevant, misleads the user
   10 = almost useless: one marginally correct detail in an otherwise wrong answer
    0 = completely wrong, irrelevant, or harmful to follow
"""

# ── Numeric claims (shared with the single judge) ─────────────────────────────
#
# Wrong *magnitudes* were invisible to every judge measured: the judge-validity
# gate's `wrong_numbers` degradation — which scales figures by 10x and swaps
# units, leaving the prose intact and the length unchanged — scored 0.60 on the
# shipped prompt (Haiku, 4 seeds, 156 pairs; 27% ties, mean score drop 1.9
# points), i.e. the judge barely saw the edit. A single 20-base run of the full
# 9-variant gate put it at 0.71; run-to-run spread on this gate is wide. It is attached to `correctness` rather than
# given its own dimension because that is where a wrong number belongs, and
# because `correctness` already carries double weight in the aggregate; a sixth
# dimension would both re-dilute the weighting and break score comparability
# with stored runs a second time.
#
# The wording names the *class* of error and deliberately no example of it. An
# earlier version listed "ms where ns is meant, MB/s where GB/s, Hz where MHz"
# and "off by an order of magnitude" — which are, verbatim, the substitutions and
# the 10x scaling the gate's `wrong_numbers` generator applies, so its measured
# gain was taken on the same perturbations the prompt described.
NUMERIC_CLAIMS = """   Check the answer's *numbers*, not only its prose: every quoted magnitude,
   unit, size, width, latency, rate or ratio is a factual claim. A figure that
   is wrong for this question — wrong value or wrong unit — is a factual error
   even when the sentence around it is fluent and confident, and caps
   correctness at 40."""

# Listed first in the output so the figures are written out before any score
# is. Criterion + enumeration moved `wrong_numbers` significantly where the
# criterion alone did not; the two were not compared head-to-head, so this is
# the best reading, not an isolated effect. Putting it in the JSON makes the
# enumeration visible in every stored judgement instead of asserted.
NUMERIC_INSTRUCTION = """- First fill `numeric_claims`: copy out every figure the answer quotes, with
  its unit (at most 20), and mark each right or wrong for this question. Then
  score correctness; a single wrong figure caps it at 40"""

# A JSON array, one entry per figure. A semicolon-separated string was tried to
# cut malformed replies (with an array first, the model sometimes closes the
# later `reasoning` object with `]`): it did — 9 extra calls per 247 judgements
# instead of 26, against 5 for the stock prompt — but it scored lower on every
# numeric variant in two independent runs, and in the replication it no longer
# beat the old wording anywhere while the array did on all three (paired
# McNemar p = 0.016 / 0.035 / 0.039). The malformed replies are retried by
# `Judge._judge_answer` and `JudgePanel._call_judge`; 0-1 judgements per 247 still fail all three attempts.
NUMERIC_OUTPUT = """  "numeric_claims": ["<figure with unit>: right|wrong", ...],"""

# The panel had none of this until it was measured against the gate's own
# `wrong_numbers` degradation (Haiku 4.5, 3 roles, 60 bases): 0.604 -> 0.792 on
# Sonnet's answers and 0.590 -> 0.795 on Opus's, pooled +25/-8 pairs, McNemar
# p = 0.0046. `wrong_tools` dipped in both runs (0.947 -> 0.895, 0.942 -> 0.904;
# pooled +5/-10, p = 0.30) — not significant, and reported, not hidden.

# ── Judge role prompts ────────────────────────────────────────────────────────

_ROLE_INTROS: Dict[str, str] = {
    "technical_expert": (
        "You are a STRICT TECHNICAL EXPERT evaluating a documentation answer.\n"
        "PRIMARY CONCERN: FACTUAL ACCURACY and TECHNICAL DEPTH.\n"
        "- Penalize vague, hand-wavy, or technically incorrect details heavily.\n"
        "- A wrong answer scores ≤ 40 on correctness even if well-written.\n"
        "- Judge 'specificity' harshly: an answer naming identifiers that do not exist in\n"
        "  __LIBRARY__, or belong to a different technology, scores ≤ 30.\n"
        "- For 'completeness': does it address ALL parts of the question?\n"
        "- For 'actionability': as a technical expert, would YOU use this answer?"
    ),
    "developer_advocate": (
        "You are a DEVELOPER ADVOCATE evaluating a documentation answer.\n"
        "PRIMARY CONCERN: PRACTICAL USEFULNESS — would a developer be unblocked right now?\n"
        "- A theoretically correct but hard-to-apply answer scores ≤ 40 on actionability.\n"
        "- Prioritize working code examples and clear step-by-step instructions.\n"
        "- For 'correctness': acceptable if minor inaccuracies don't affect usability.\n"
        "- For 'completeness': enough to get started? Partial answers can score 70+ if actionable."
    ),
    "doc_reviewer": (
        "You are a DOCUMENTATION QUALITY REVIEWER evaluating a documentation answer.\n"
        "PRIMARY CONCERN: COMPLETENESS and FIDELITY TO THE PROVIDED CONTEXT.\n"
        "- An answer ignoring key aspects of the question scores ≤ 40 on completeness.\n"
        "- Check: does the answer stay within the scope of the provided documentation context?\n"
        "- Penalize answers that introduce information NOT present in the context.\n"
        "- For 'specificity': are the __LIBRARY__ APIs it names real and used correctly?"
    ),
}

_PROMPT_TEMPLATE = """\
__ROLE_INTRO__

Library: __LIBRARY__
Question: __QUESTION__
Answer: __ANSWER__
Documentation context: __CONTEXT__

Rate each dimension 0–100. Use these calibration anchors:
__ANCHORS__

Dimensions to rate:
1. correctness   — Is the answer factually accurate for __LIBRARY__?
__NUMERIC_CLAIMS__
2. completeness  — Does it address ALL parts of the question?
3. specificity   — Do the identifiers it names — APIs, functions, flags, tools —
                   actually belong to __LIBRARY__ and fit this question? Naming
                   __LIBRARY__ often is not specificity; a plausible-looking
                   identifier that is wrong for __LIBRARY__ scores low.
4. code_quality  — Is the code correct, runnable, idiomatic __LIBRARY__ code?
                   IF NO CODE IN THE ANSWER: set code_quality = 100 (not applicable).
5. actionability — Can the user directly apply this to their problem right now?

IMPORTANT: Output ONLY a JSON object. Do NOT wrap in markdown. Do NOT add any text before or after.
__NUMERIC_INSTRUCTION__
Put "numeric_claims" first, then "reasoning":
{__NUMERIC_OUTPUT__ "reasoning": "one sentence: main strength or weakness", "correctness": N, "completeness": N, "specificity": N, "code_quality": N, "actionability": N}
"""

JUDGE_ROLES: Dict[str, str] = {
    role: _PROMPT_TEMPLATE
    .replace("__ROLE_INTRO__", intro)
    .replace("__ANCHORS__", SCORE_ANCHORS)
    .replace("__NUMERIC_CLAIMS__", NUMERIC_CLAIMS)
    .replace("__NUMERIC_INSTRUCTION__", NUMERIC_INSTRUCTION)
    .replace("__NUMERIC_OUTPUT__", NUMERIC_OUTPUT.strip())
    for role, intro in _ROLE_INTROS.items()
}

DEFAULT_PANEL = ["technical_expert", "developer_advocate", "doc_reviewer"]

# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class JudgeVote:
    role: str
    model: str
    provider: str
    scores: Dict[str, float]    # raw scores only (no aggregate — computed by Python)
    reasoning: str
    aggregate: Optional[float] = None   # Python-computed weighted aggregate
    error: Optional[str] = None
    #: The judged score a cap replaced, and the indexes of `numeric_claims`
    #: entries with no readable verdict — the same fields `Judge` records.
    caps: Optional[Dict[str, Any]] = None
    unread_claims: Optional[List[int]] = None
    #: The figures the judge wrote out, as replied. Kept so a cap, or an
    #: `unread_claims` index, can be traced to the figure behind it.
    numeric_claims: Optional[List[Any]] = None


@dataclass
class PanelVerdict:
    votes: List[JudgeVote]
    mean_aggregate: Optional[float]
    std_aggregate: Optional[float]
    agreement_score: Optional[float]   # 0–1; 1.0 = perfect agreement
    mean_dimensions: Dict[str, float] = field(default_factory=dict)

    @property
    def valid_votes(self) -> List[JudgeVote]:
        return [v for v in self.votes if v.aggregate is not None]

    @property
    def disagreement_flag(self) -> bool:
        return self.std_aggregate is not None and self.std_aggregate > 15.0


@dataclass
class JudgeConfig:
    role: str
    model: str
    provider: str
    temperature: Optional[float] = None
    seed: Optional[int] = None


# ── Panel ─────────────────────────────────────────────────────────────────────

class JudgePanel:
    """Run multiple LLM judges in parallel and aggregate their verdicts."""

    def __init__(
        self,
        judges: Optional[List[JudgeConfig]] = None,
        default_model: str = "gpt-4o-mini",
        default_provider: str = "openai",
        concurrency: int = 6,
        temperature: Optional[float] = None,
        seed: Optional[int] = None,
    ):
        self.judges = judges or [
            JudgeConfig(role=role, model=default_model, provider=default_provider,
                       temperature=temperature, seed=seed)
            for role in DEFAULT_PANEL
        ]
        self.concurrency = concurrency

    @property
    def model(self) -> str:
        """Panel identity, in the slot a single judge's model name occupies.

        ``ArmRunner.build_output`` records ``judge.model``/``judge.provider`` as
        the artifact's judge provenance, and ``replicate_key`` keys on those. A
        panel exposing neither wrote nulls, which made two runs judged by
        *different* panels indistinguishable — and so poolable as replicates.
        Roles are listed in configured order; models are collapsed when the
        panel shares one.
        """
        roles = "+".join(j.role for j in self.judges)
        models = sorted({j.model for j in self.judges})
        return f"panel[{roles}]:{models[0] if len(models) == 1 else ','.join(models)}"

    @property
    def prompt_sha256(self) -> str:
        """Digest of the role prompts this panel sends, in configured order."""
        text = "\0".join(JUDGE_ROLES.get(j.role, JUDGE_ROLES["technical_expert"]) for j in self.judges)
        return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()

    @property
    def temperature(self) -> Optional[float]:
        """The judges' shared temperature, or None when they differ or none is pinned."""
        temps = {j.temperature for j in self.judges}
        return temps.pop() if len(temps) == 1 else None

    @property
    def provider(self) -> str:
        providers = sorted({j.provider for j in self.judges})
        return providers[0] if len(providers) == 1 else "mixed:" + ",".join(providers)

    def _build_prompt(self, role: str, question: str, answer: str,
                      library_name: str, context: str) -> str:
        template = JUDGE_ROLES.get(role, JUDGE_ROLES["technical_expert"])
        # Use sequential replace with unique sentinels to avoid injection
        # (each placeholder is distinct so no cross-substitution is possible)
        return (
            template
            .replace("__LIBRARY__", library_name)
            .replace("__QUESTION__", question)
            .replace("__ANSWER__", answer)
            .replace("__CONTEXT__", context or "(No documentation provided)")
        )

    @staticmethod
    def _compute_aggregate(role: str, scores: Dict[str, float]) -> float:
        """Compute weighted aggregate in Python — never trust LLM to do math."""
        weights = ROLE_WEIGHTS.get(role, {k: 1.0 for k in SCORE_FIELDS})
        total_weight = sum(weights.values())
        weighted_sum = sum(scores.get(k, 0.0) * weights.get(k, 1.0) for k in SCORE_FIELDS)
        return round(weighted_sum / total_weight, 1)

    @staticmethod
    def _extract_json(text: str) -> Dict:
        from dokimi.llm import extract_json_object
        return extract_json_object(text)

    def _call_judge(self, config: JudgeConfig, question: str, answer: str,
                    library_name: str, context: str) -> JudgeVote:
        prompt = self._build_prompt(config.role, question, answer, library_name, context)

        def parse(raw: str) -> "tuple[Dict[str, Any], Dict[str, float]]":
            data = self._extract_json(raw)
            if not isinstance(data, dict):
                raise ValueError("judge reply is not a JSON object")
            missing = [k for k in SCORE_FIELDS if k not in data]
            if missing:
                raise ValueError(f"Missing keys in judge response: {missing}")
            # A non-numeric ("N/A"), boolean or non-finite dimension is a
            # formatting failure too; the clamp alone would turn NaN into 100.
            raw_scores = {k: data[k] for k in SCORE_FIELDS}
            if any(isinstance(v, bool) or not math.isfinite(float(v))
                   for v in raw_scores.values()):
                raise ValueError("judge reply has a non-numeric or non-finite score")
            scores = {k: max(0.0, min(100.0, float(v))) for k, v in raw_scores.items()}
            return data, scores

        def parses(raw: str) -> bool:
            # Only a reply that yields a vote is cached: a stored unparseable one
            # would be replayed on every later run with the cache on.
            try:
                parse(raw)
            except Exception:
                return False
            return True

        # judge.py imports this module, so its helpers are read here, not at import.
        from dokimi.eval.judge import _FORMAT_REMINDER, Judge

        pinned = config.temperature is not None or config.seed is not None
        try:
            # Retry an unparseable reply rather than losing this role's vote to a
            # formatting failure, as ``Judge.score_answer`` does. Each attempt is
            # its own response-cache draw: under one cache key the retries replayed
            # the same unparseable reply. With sampling pinned the same prompt
            # returns the same reply live, so a re-draw then appends a reminder.
            last_parse_exc: Exception = ValueError("panel retry loop did not execute")
            for attempt in range(3):
                attempt_prompt = prompt
                if attempt and pinned:
                    attempt_prompt = prompt + _FORMAT_REMINDER.format(n=attempt)
                raw = llm_call(attempt_prompt, model=config.model, provider=config.provider,
                               temperature=config.temperature, seed=config.seed,
                               draw=attempt, cache_if=parses)
                try:
                    data, scores = parse(raw)
                    break
                except Exception as parse_exc:
                    last_parse_exc = parse_exc
                    logger.warning(
                        "Judge '%s' reply unparseable (attempt %d/3): %s",
                        config.role, attempt + 1, parse_exc,
                    )
            else:
                raise last_parse_exc
            # Hold the prompt's wrong-figure cap in Python, with the single
            # judge's own rule, so the two cannot disagree about what it means.
            capped: Dict[str, Any] = {"correctness": scores["correctness"],
                                      "numeric_claims": data.get("numeric_claims")}
            Judge._cap_for_wrong_figures(capped)
            scores["correctness"] = capped["correctness"]
            # Python computes aggregate (ignores any aggregate LLM might have returned)
            agg = self._compute_aggregate(config.role, scores) if scores else None
            return JudgeVote(
                role=config.role, model=config.model, provider=config.provider,
                scores=scores, reasoning=str(data.get("reasoning", "")), aggregate=agg,
                caps=capped.get("caps"), unread_claims=capped.get("unread_claims"),
                numeric_claims=claims if isinstance(claims := data.get("numeric_claims"), list) else None,
            )
        except Exception as exc:
            logger.warning(f"Judge '{config.role}' ({config.model}) failed: {exc}")
            return JudgeVote(
                role=config.role, model=config.model, provider=config.provider,
                scores={}, reasoning="", error=str(exc),
            )

    def score_answer(
        self,
        library_name: str,
        question: str,
        answer: str,
        context: str = "(No documentation retrieved)",
        ground_truth: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Score one answer, shaped like :meth:`Judge.score_answer`.

        Lets a panel stand in wherever a single ``Judge`` is expected — notably
        ``ArmRunner.judge``, which reaches for ``score_answer`` and previously
        made panels unusable from the arms track. ``aggregate`` carries the
        panel mean so existing readers need no change; the panel-only fields
        (spread, agreement, per-role votes) are added alongside, and are the
        reason to prefer a panel: they expose grader disagreement that a single
        judge cannot report.

        ``ground_truth`` is accepted for signature compatibility and ignored —
        the panel's per-role prompts do not currently take a ground-truth chunk.
        """
        verdict = self.evaluate(
            question=question, answer=answer,
            library_name=library_name, context=context,
        )
        out: Dict[str, Any] = dict(verdict.mean_dimensions)
        out["aggregate"] = verdict.mean_aggregate
        out["panel"] = {
            "n_judges": len(verdict.votes),
            "n_valid": len(verdict.valid_votes),
            "std_aggregate": verdict.std_aggregate,
            "agreement_score": verdict.agreement_score,
            "disagreement_flag": verdict.disagreement_flag,
            "votes": [
                {"role": v.role, "model": v.model, "provider": v.provider,
                 "aggregate": v.aggregate, "error": v.error, **_numeric_fields(v)}
                for v in verdict.votes
            ],
        }
        if verdict.mean_aggregate is None:
            out["error"] = "all panel judges failed"
        return out

    def evaluate(self, question: str, answer: str, library_name: str,
                 context: str = "") -> PanelVerdict:
        votes: List[JudgeVote] = []
        with ThreadPoolExecutor(max_workers=self.concurrency) as pool:
            futures = {
                pool.submit(self._call_judge, cfg, question, answer, library_name, context): cfg
                for cfg in self.judges
            }
            for future in as_completed(futures):
                votes.append(future.result())
        return self._aggregate(votes)

    def _aggregate(self, votes: List[JudgeVote]) -> PanelVerdict:
        valid = [v for v in votes if v.aggregate is not None]
        if not valid:
            return PanelVerdict(votes=votes, mean_aggregate=None,
                                std_aggregate=None, agreement_score=None)
        aggs = [v.aggregate for v in valid]
        mean_agg = round(statistics.mean(aggs), 1)
        std_agg = round(statistics.stdev(aggs), 1) if len(aggs) > 1 else 0.0
        agreement = round(max(0.0, 1.0 - std_agg / 50.0), 3)
        mean_dims: Dict[str, float] = {}
        for dim in SCORE_FIELDS:
            vals = [v.scores[dim] for v in valid if dim in v.scores]
            if vals:
                mean_dims[dim] = round(statistics.mean(vals), 1)
        return PanelVerdict(votes=votes, mean_aggregate=mean_agg,
                            std_aggregate=std_agg, agreement_score=agreement,
                            mean_dimensions=mean_dims)

    def _score_answer_entry(self, ans_data: Optional[Dict], question: str,
                            library_name: str) -> Optional[PanelVerdict]:
        if not ans_data or not ans_data.get("answer"):
            return None
        # answers.v1 keeps only each doc's 200-char ``snippet``; the full text the
        # answerer was given is not persisted. That is also what the single judge
        # shows (``Judge._format_context``), so both judges see the same material.
        # ``content`` is read as a fallback for records that do carry it.
        ctx = "\n".join(
            d.get("snippet") or d.get("content", "")
            for d in ans_data.get("retrieved_docs") or [] if d
        )
        return self.evaluate(question, ans_data["answer"], library_name, ctx)

    def evaluate_answers(self, answers: List[Dict[str, Any]], library_name: str,
                         output_path: Optional[Path] = None,
                         limit: Optional[int] = None) -> List[Dict[str, Any]]:
        if limit:
            answers = answers[:limit]
        results = []
        n = len(answers)
        for i, answer in enumerate(answers, 1):
            q_id = answer.get("question_id", answer.get("id", f"q{i}"))
            # answers.v1 writes ``question_text``; ``question``/``text`` are the
            # question-set spellings, accepted for hand-built inputs.
            question = (
                answer.get("question_text") or answer.get("question") or answer.get("text", "")
            )
            print(f"[{i}/{n}] Panel: {q_id}…", flush=True)
            with_v = self._score_answer_entry(answer.get("with_docs"), question, library_name)
            without_v = self._score_answer_entry(answer.get("without_docs"), question, library_name)
            delta = None
            if (with_v and with_v.mean_aggregate is not None
                    and without_v and without_v.mean_aggregate is not None):
                delta = round(with_v.mean_aggregate - without_v.mean_aggregate, 1)
            results.append({
                "question_id": q_id,
                "question": question,
                "panel_size": len(self.judges),
                "with_docs": _verdict_to_dict(with_v),
                "without_docs": _verdict_to_dict(without_v),
                "delta": delta,
                "retrieval_hit": retrieval_hit(answer),
            })
            if output_path:
                _save_incremental(results, output_path, library_name, self)
        return results


# ── Helpers ───────────────────────────────────────────────────────────────────

def _numeric_fields(vote: JudgeVote) -> Dict[str, Any]:
    """The vote's figure check, when it made one: what it listed and what that capped."""
    return {key: value for key, value in (("numeric_claims", vote.numeric_claims),
                                          ("caps", vote.caps),
                                          ("unread_claims", vote.unread_claims)) if value}


def _verdict_to_dict(v: Optional[PanelVerdict]) -> Optional[Dict[str, Any]]:
    if v is None:
        return None
    return {
        "aggregate": v.mean_aggregate,
        "std": v.std_aggregate,
        "agreement_score": v.agreement_score,
        "disagreement_flag": v.disagreement_flag,
        "dimensions": v.mean_dimensions,
        "votes": [
            {"role": vt.role, "model": vt.model, "aggregate": vt.aggregate,
             "scores": vt.scores, "reasoning": vt.reasoning, "error": vt.error,
             **_numeric_fields(vt)}
            for vt in v.votes
        ],
    }


def _save_incremental(results: List[Dict], path: Path, library_name: str,
                      panel: JudgePanel) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    output = {
        "evaluated_at": _timestamp(),
        "judge_model": f"panel({len(panel.judges)})",
        "judge_temperature": panel.temperature,
        "panel_config": {
            "size": len(panel.judges),
            "roles": [j.role for j in panel.judges],
            "models": [f"{j.provider}/{j.model}" for j in panel.judges],
            "prompt_sha256": panel.prompt_sha256,
        },
        "library_name": library_name,
        "total_evaluations": len(results),
        "evaluations": results,
    }
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2)
    os.replace(tmp, path)


def _timestamp() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()
