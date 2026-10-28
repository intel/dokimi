# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Characterization of the two answering stacks (#228 step 1). No behaviour is changed here.

Both stacks answer and judge the same six-question fixture. Today's values of
every difference listed in #228 are pinned in :data:`PINNED`, one row per
observation, labelled with the #228 difference it belongs to (D1-D9), or
"shared"/"only" for the parts that match or exist on one side only.

A later PR that changes one difference flips the rows for that difference, and
only the rows whose comment names it (the D9 delta also moves with D3, see the
table), so the diff of that PR shows the change in numbers. Each row is
``(difference, key, p_value, a_value)``: P is Stack P (``EvaluationPipeline``
plus ``Answerer``), A is Stack A (``ArmRunner``).
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import pytest

from dokimi import llm
from tests.answering_stack_parity import (
    DOCS_QUERY,
    EMPTY_QUERY,
    GROUND_TRUTH_CHUNK,
    GROUND_TRUTH_QUERY,
    PRODUCT,
    QUESTIONS,
    RERANK_QUERY,
    REGISTRY_ID,
    Observed,
    answer_calls,
    answer_record,
    classify_answer,
    context_shape,
    distinct,
    docs_block,
    judge_context_strings,
    judge_inputs_for,
    prompt_shape,
    request_shape,
    run_stack_a,
    run_stack_p,
)

QTEXT = {q["id"]: q["question"] for q in QUESTIONS}
DIFFERENCES = {f"D{i}" for i in range(1, 10)}

# (difference, key, value on Stack P, value on Stack A). Pinned at main b69b15f8.
PINNED: Tuple[Tuple[str, str, Any, Any], ...] = (
    # shared: the two stacks agree today
    ("shared", "retrieval.count", 6, 6),
    ("shared", "reranker.calls", 5, 5),
    ("shared", "answer.roles", ("user",), ("user",)),
    ("shared", "answer.system", None, None),
    ("shared", "answer.cache_blocks", False, False),
    # D1 retrieval request
    ("D1", "retrieval.passed", ("max_results", "max_tokens"), ("max_results",)),
    # what each side asked for, not what came back: both get 4 docs; both ask for top_k=3
    ("D1", "retrieval.max_results", 3, 3),
    ("D1", "retrieval.max_tokens", 4000, 8000),
    # D2 no docs found
    ("D2", "empty.answer_kinds", ("plain",), ("plain", "plain")),
    ("D2", "empty.with_docs_answer", "placeholder", "model_reply"),
    # D3 rerank raises
    ("D3", "rerank.answer_kinds", ("plain",), ("plain", "docs")),
    ("D3", "rerank.with_docs_answer", "placeholder", "model_reply"),
    # D4 answer call and prompts
    ("D4", "answer.max_tokens", None, 4000),
    ("D4", "answer.temperature", None, 0.0),
    ("D4", "answer.seed", None, 1),
    (
        "D4",
        "docs_prompt.first_line",
        "You are a technical documentation expert answering a user question.",
        "You are answering a user question.",
    ),
    ("D4", "docs_prompt.order", ("question", "documentation"), ("context", "question")),
    ("D4", "docs_prompt.guidance", ("only_docs",), ("where_relevant",)),
    (
        "D4",
        "plain_prompt.first_line",
        "You are a technical expert answering a user question based on your knowledge.",
        "Answer the following user question. Be specific and include code examples if relevant.",
    ),
    # D5 what the judge sees
    (
        "D5",
        "judge.docs_context.q-docs",
        {"starts_with": "Doc 1: ", "chunks": 3, "whole_passage": False},
        {"starts_with": "Passage", "chunks": 3, "whole_passage": True},
    ),
    (
        "D5",
        "judge.baseline_context.q-docs",
        "(No documentation provided)",
        "(No documentation retrieved)",
    ),
    # D6 judge temperature (CLI: P = benchmark run, A = arms run)
    ("D6", "cli.judge_temperature_flag", True, True),
    # D7 --max-tokens (CLI defaults; where the configured value lands)
    ("D7", "cli.max_tokens_default", 4000, None),
    ("D7", "max_tokens.lands_in", "retrieval", "answer"),
    # D8 library id: EvaluationPipeline with no context7_id (the `orchestrate` path without
    # --context7-id). `benchmark run` passes the registry's context7_id, so it is not this row.
    ("D8", "library_id", "registry/lib", "registry/lib"),
    # D2 judge calls: P no longer judges the placeholder with-docs answers of q-empty and
    # q-rerank (D2, D3), so 2 of the 12 calls are gone. A still judges all 12.
    ("D2", "judge.calls", 10, 12),
    # D9 statistics. P's paired deltas are q-docs, q-docs-2, q-docs-3, q-gt: 4 pairs, below
    # MIN_PAIRS_FOR_TEST (6), so P has no significance test. A has 6 pairs.
    ("D9", "stats.delta_mean", 20.0, 16.67),
    ("D9", "stats.pairs", 4, 6),
    ("D9", "significance.n", None, 6),
    ("D9", "significance.correction", None, "holm"),
    ("D9", "significance.significant", None, False),
    # what the judge sees per case: no docs (D2), reranker raises (D3), ground-truth chunk (D5)
    (
        "D2",
        "judge.contexts.q-empty",
        ("(No documentation provided)",),
        ("(No documentation retrieved)",),
    ),
    (
        "D3",
        "judge.contexts.q-rerank",
        ("(No documentation provided)",),
        ("(No documentation retrieved)", ("docs", "Passage", 3, True)),
    ),
    (
        "D5",
        "judge.contexts.q-gt",
        ("(No documentation provided)", ("docs", "Doc 1: ", 3, False)),
        ("(No documentation retrieved)", ("docs", "Passage", 3, True)),
    ),
    # what the judge is handed as ground truth: the q-gt chunk on both stacks, None on the rest
    ("shared", "judge.ground_truth.q-gt", (GROUND_TRUTH_CHUNK,), (GROUND_TRUTH_CHUNK,)),
    ("shared", "judge.ground_truth.others", (None,), (None,)),
    # the per-question deltas that feed D9's statistics (P: stored "delta"; A: "docs" vs "baseline").
    # q-empty and q-rerank have no P delta (no with-docs judgement, D2/D3); A's q-empty is 0 and
    # its q-rerank falls back to the raw docs (20).
    (
        "D9",
        "delta.per_question",
        (
            ("q-docs", 20.0),
            ("q-docs-2", 20.0),
            ("q-docs-3", 20.0),
            ("q-empty", None),
            ("q-gt", 20.0),
            ("q-rerank", None),
        ),
        (
            ("q-docs", 20.0),
            ("q-docs-2", 20.0),
            ("q-docs-3", 20.0),
            ("q-empty", 0.0),
            ("q-gt", 20.0),
            ("q-rerank", 20.0),
        ),
    ),
    # only in one stack
    ("only", "record.shape", "with_docs/without_docs", "arms"),
    ("only", "answer.token_usage.context_chars", True, False),
    ("only", "artifact.cost_summary", False, True),
    # #228 step 2 stamp: the answers artifact carries method_version; the arms artifact does not
    ("only", "artifact.method_version", 2, None),
)


def _subparser(*words: str) -> argparse.ArgumentParser:
    from dokimi.cli import build_parser

    parser = build_parser()
    for word in words:
        action = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
        parser = action.choices[word]
    return parser


def _has_option(parser: argparse.ArgumentParser, flag: str) -> bool:
    return any(flag in a.option_strings for a in parser._actions)


def _default_or_absent(parser: argparse.ArgumentParser, dest: str) -> Any:
    """The default of the ``--<dest>`` option, or "absent" when the parser has no such option."""
    if not _has_option(parser, "--" + dest.replace("_", "-")):
        return "absent"
    return parser.get_default(dest)


def _all_answer_requests(obs: Observed):
    out = []
    for q in QUESTIONS:
        out.extend(answer_calls(obs, q["question"]))
    return out


def _kinds(obs: Observed, qid: str) -> Tuple[str, ...]:
    return tuple(kind for kind, _ in answer_calls(obs, QTEXT[qid]))


def _docs_context(obs: Observed, question: str) -> Dict[str, Any]:
    docs = [
        c for c in judge_context_strings(obs, question) if not c.startswith("(No documentation")
    ]
    assert len(docs) == 1, docs
    return context_shape(docs[0], question)


def _baseline_context(obs: Observed, question: str) -> str:
    sentinel = [
        c for c in judge_context_strings(obs, question) if c.startswith("(No documentation")
    ]
    assert len(sentinel) == 1, sentinel
    return sentinel[0]


def _context_kinds(obs: Observed, question: str) -> Tuple[Any, ...]:
    """Each distinct judge context: a sentinel string verbatim, or ("docs", opening, chunks, first passage whole)."""
    kinds = []
    for c in judge_context_strings(obs, question):
        if c.startswith("(No documentation"):
            kinds.append(c)
        else:
            shape = context_shape(c, question)
            kinds.append(("docs", shape["starts_with"], shape["chunks"], shape["whole_passage"]))
    return tuple(sorted(kinds, key=repr))


def _judge_ground_truths(obs: Observed, question: str) -> Tuple[Any, ...]:
    """The distinct ``ground_truth`` values the judge was handed for one question."""
    return tuple(sorted({j.get("ground_truth") for j in judge_inputs_for(obs, question)}, key=repr))


def _judge_ground_truths_elsewhere(obs: Observed) -> Tuple[Any, ...]:
    """The distinct ``ground_truth`` values the judge was handed for every other question."""
    seen = set()
    for q in QUESTIONS:
        if q["question"] != GROUND_TRUTH_QUERY:
            seen |= {j.get("ground_truth") for j in judge_inputs_for(obs, q["question"])}
    return tuple(sorted(seen, key=repr))


def _where_4000_lands(obs: Observed) -> str:
    """Every place the configured 4000 reached: doc-source retrieval and/or the model answer call.

    Returns the places sorted and joined with "+", or "neither".
    """
    retrieval = {c["max_tokens"] for c in obs.client.calls}
    answer = {request_shape(r)["max_tokens"] for _, r in _all_answer_requests(obs)}
    places = [name for name, seen in (("answer", answer), ("retrieval", retrieval)) if 4000 in seen]
    return "+".join(places) if places else "neither"


def _observe(tmp_path: Path, provider_calls) -> Dict[str, Tuple[Any, Any]]:
    from dokimi.commands.arms import _resolve_doc_library_id
    from dokimi.registry import ProductRegistry
    from dokimi.treatments.arms import DocTreatment

    registry_path = tmp_path / "products.yaml"
    registry_path.write_text(
        f"products:\n  {PRODUCT}:\n    name: Fixture library\n"
        f"    description: fixture\n    context7_id: {REGISTRY_ID}\n",
        encoding="utf-8",
    )
    registry = ProductRegistry(registry_path)

    with pytest.MonkeyPatch.context() as mp:
        p = run_stack_p(mp, tmp_path / "p", provider_calls)
    with pytest.MonkeyPatch.context() as mp:
        a = run_stack_a(mp, tmp_path / "a", provider_calls)
    with pytest.MonkeyPatch.context() as mp:
        p_noid = run_stack_p(
            mp,
            tmp_path / "p-noid",
            provider_calls,
            context7_id=None,
            questions=QUESTIONS[:1],
            registry=registry,
        )

    a_lib = _resolve_doc_library_id(
        [DocTreatment(a.client, name="docs", top_k=3, reranker=a.reranker)],
        PRODUCT,
        registry=ProductRegistry(registry_path),
    )

    p_all = _all_answer_requests(p)
    a_all = _all_answer_requests(a)
    p_docs = [r for k, r in p_all if k == "docs"]
    a_docs = [r for k, r in a_all if k == "docs"]
    p_plain = [r for k, r in p_all if k == "plain"]
    a_plain = [r for k, r in a_all if k == "plain"]

    def prompt_field(reqs, field):
        return distinct([prompt_shape(llm.prompt_text(r))[field] for r in reqs])

    def sampling(pairs, field):
        return distinct([request_shape(r)[field] for _, r in pairs])

    p_rec_empty = answer_record(p, "q-empty")
    p_rec_docs = answer_record(p, "q-docs")
    a_rec_docs = answer_record(a, "q-docs")
    a_rec_empty = answer_record(a, "q-empty")
    p_rec_rerank = answer_record(p, "q-rerank")
    a_rec_rerank = answer_record(a, "q-rerank")

    cli_run_p = _subparser("benchmark", "run")
    cli_run_a = _subparser("arms", "run")

    sig_p = _significance_p(p)
    sig_a = a.artifact["summary"]["per_arm"]["docs"]["significance"]
    paired_p = _paired_deltas(p)

    return {
        "retrieval.count": (len(p.client.calls), len(a.client.calls)),
        "judge.calls": (len(p.judge_inputs), len(a.judge_inputs)),
        "reranker.calls": (len(p.reranker.calls), len(a.reranker.calls)),
        "answer.roles": (sampling(p_all, "roles"), sampling(a_all, "roles")),
        "answer.system": (sampling(p_all, "system"), sampling(a_all, "system")),
        "answer.cache_blocks": (sampling(p_all, "cache_blocks"), sampling(a_all, "cache_blocks")),
        "stats.delta_mean": (round(sum(paired_p) / len(paired_p), 2), sig_a["delta_mean"]),
        "stats.pairs": (len(paired_p), sig_a["n"]),
        "retrieval.passed": (
            distinct([c["passed"] for c in p.client.calls]),
            distinct([c["passed"] for c in a.client.calls]),
        ),
        "retrieval.max_results": (
            distinct([c["max_results"] for c in p.client.calls]),
            distinct([c["max_results"] for c in a.client.calls]),
        ),
        "retrieval.max_tokens": (
            distinct([c["max_tokens"] for c in p.client.calls]),
            distinct([c["max_tokens"] for c in a.client.calls]),
        ),
        "empty.answer_kinds": (_kinds(p, "q-empty"), _kinds(a, "q-empty")),
        "empty.with_docs_answer": (
            classify_answer(docs_block(p, p_rec_empty)["answer"]),
            classify_answer(docs_block(a, a_rec_empty)["answer"]),
        ),
        "rerank.answer_kinds": (_kinds(p, "q-rerank"), _kinds(a, "q-rerank")),
        "rerank.with_docs_answer": (
            classify_answer(docs_block(p, p_rec_rerank)["answer"]),
            classify_answer(docs_block(a, a_rec_rerank)["answer"]),
        ),
        "answer.max_tokens": (sampling(p_all, "max_tokens"), sampling(a_all, "max_tokens")),
        "answer.temperature": (sampling(p_all, "temperature"), sampling(a_all, "temperature")),
        "answer.seed": (sampling(p_all, "seed"), sampling(a_all, "seed")),
        "docs_prompt.first_line": (
            prompt_field(p_docs, "first_line"),
            prompt_field(a_docs, "first_line"),
        ),
        "docs_prompt.order": (prompt_field(p_docs, "order"), prompt_field(a_docs, "order")),
        "docs_prompt.guidance": (
            prompt_field(p_docs, "guidance"),
            prompt_field(a_docs, "guidance"),
        ),
        "plain_prompt.first_line": (
            prompt_field(p_plain, "first_line"),
            prompt_field(a_plain, "first_line"),
        ),
        "judge.docs_context.q-docs": (_docs_context(p, DOCS_QUERY), _docs_context(a, DOCS_QUERY)),
        "judge.baseline_context.q-docs": (
            _baseline_context(p, DOCS_QUERY),
            _baseline_context(a, DOCS_QUERY),
        ),
        "cli.judge_temperature_flag": (
            _has_option(cli_run_p, "--judge-temperature"),
            _has_option(cli_run_a, "--judge-temperature"),
        ),
        "cli.max_tokens_default": (
            _default_or_absent(cli_run_p, "max_tokens"),
            _default_or_absent(cli_run_a, "max_tokens"),
        ),
        "max_tokens.lands_in": (_where_4000_lands(p), _where_4000_lands(a)),
        "library_id": (p_noid.client.calls[0]["library_id"], a_lib),
        # None: the paired test does not run below MIN_PAIRS_FOR_TEST pairs (P has 4 here)
        "significance.n": (sig_p["n"] if sig_p else None, sig_a["n"]),
        "significance.correction": (
            sig_p.get("correction") if sig_p else None,
            sig_a.get("correction"),
        ),
        "significance.significant": (
            sig_p["significant"] if sig_p else None,
            sig_a["significant"],
        ),
        "record.shape": (_record_shape(p_rec_empty), _record_shape(a_rec_empty)),
        "answer.token_usage.context_chars": (
            "context_chars" in docs_block(p, p_rec_docs)["token_usage"],
            "context_chars" in docs_block(a, a_rec_docs)["token_usage"],
        ),
        "artifact.cost_summary": ("cost_summary" in p.artifact, "cost_summary" in a.artifact),
        "artifact.method_version": (p.artifact.get("method_version"), a.artifact.get("method_version")),
        "judge.contexts.q-empty": (_context_kinds(p, EMPTY_QUERY), _context_kinds(a, EMPTY_QUERY)),
        "judge.contexts.q-rerank": (
            _context_kinds(p, RERANK_QUERY),
            _context_kinds(a, RERANK_QUERY),
        ),
        "judge.contexts.q-gt": (
            _context_kinds(p, GROUND_TRUTH_QUERY),
            _context_kinds(a, GROUND_TRUTH_QUERY),
        ),
        "judge.ground_truth.q-gt": (
            _judge_ground_truths(p, GROUND_TRUTH_QUERY),
            _judge_ground_truths(a, GROUND_TRUTH_QUERY),
        ),
        "judge.ground_truth.others": (
            _judge_ground_truths_elsewhere(p),
            _judge_ground_truths_elsewhere(a),
        ),
        "delta.per_question": (
            tuple(sorted((e["question_id"], e["delta"]) for e in p.evaluations)),
            # Stack A's judge.score_answer deltas: the "docs" arm against the "baseline" arm
            tuple(
                sorted(
                    (r["question_id"], r["deltas_vs_baseline"].get("docs")) for r in a.evaluations
                )
            ),
        ),
    }


def _significance_p(obs: Observed) -> Optional[Dict[str, Any]]:
    """Stack P's paired test; None when it does not run (fewer pairs than MIN_PAIRS_FOR_TEST)."""
    from dokimi.report.eval_report import significance_test

    return significance_test(obs.evaluations)


def _paired_deltas(obs: Observed) -> List[float]:
    """The deltas of the questions both arms were judged on."""
    return [e["delta"] for e in obs.evaluations if e.get("delta") is not None]


def _record_shape(rec: Dict[str, Any]) -> str:
    return "arms" if "arms" in rec else "with_docs/without_docs"


def test_both_stacks_match_pinned_values(tmp_path, provider_calls):
    observed = _observe(tmp_path, provider_calls)
    mismatches = []
    for diff, key, p_expected, a_expected in PINNED:
        if key not in observed:
            mismatches.append(f"{diff} {key}: no observation (typo in PINNED?)")
            continue
        got_p, got_a = observed[key]
        if (got_p, got_a) != (p_expected, a_expected):
            mismatches.append(
                f"{diff} {key}: P pinned {p_expected!r}, got {got_p!r}; "
                f"A pinned {a_expected!r}, got {got_a!r}"
            )
    assert not mismatches, "\n".join(mismatches)


def test_table_covers_every_difference():
    labels = {diff for diff, *_ in PINNED}
    assert DIFFERENCES <= labels, DIFFERENCES - labels
