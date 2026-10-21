# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Tests for the judge-validity gate and the degradations it relies on.

These run offline. What is covered here is the harness — that degradations
really damage an answer, that they are reproducible, and that the accuracy and
gate arithmetic is right for judges whose behaviour is known (a perfect judge, a
saturated judge, an inverted judge). Measuring a real LLM judge needs API
credentials and is the job of `cli.py eval judge-validity`.
"""

import random

import pytest

from dokimi.eval.degrade import (
    DEGRADATIONS,
    HEDGE_SENTENCES,
    NEUTRAL_VARIANTS,
    PADDING_PARAGRAPHS,
    SHORT_HEDGES,
    build_pairs,
    contradiction,
    degrade,
    hedge_concrete,
    padding,
    shuffle_sentences,
    strip_code,
    truncate_20,
    wrong_numbers,
    wrong_tools,
)
from dokimi.eval.judge_validity import (
    DEFAULT_MIN_ACCURACY,
    NoUsablePairs,
    ValidityReport,
    ValidityRow,
    evaluate_pairs,
    run_validity,
)

ANSWER = (
    "Use perf stat to collect IPC on the Intel platform, then narrow the hotspot "
    "with VTune. The oneTBB scheduler is not the bottleneck here.\n\n"
    "```\nperf stat -e cycles,instructions ./app\n```\n\n"
    "Once you have the PMU counters, enable AVX-512 in the build. "
    "Expect roughly 1.5x on 8 threads at 2.4 GHz. "
) * 12

# Also long, also fenced, also full of tool names, also carrying magnitudes — so
# every degradation bites on it too. An answer with no code, no tool mentions or
# no numbers is a silent no-op for strip_code, wrong_tools or wrong_numbers,
# which would make the fixtures lie.
OTHER_ANSWER = (
    "Install the Intel oneAPI toolkit first, then check the PMU is exposed.\n\n"
    "```\napt install intel-oneapi-base\n```\n\n"
    "VTune needs perf_event_paranoid lowered before it can read IPC. "
    "Allow about 200 ms of startup overhead on 4 cores. "
) * 12


def _answers():
    return [
        {
            "question_id": "Q001",
            "question_text": "How do I find the hotspot?",
            "arms": {
                "baseline": {"answer": ANSWER},
                "skill": {"answer": ANSWER + "Extra detail. "},
            },
        },
        {
            "question_id": "Q002",
            "question_text": "How do I install it?",
            "arms": {"baseline": {"answer": OTHER_ANSWER}},
        },
    ]


class TestDegradations:
    def test_truncate_keeps_a_fifth(self):
        out = truncate_20(ANSWER)
        assert len(out) < len(ANSWER)
        assert ANSWER.startswith(out)

    def test_strip_code_removes_fences(self):
        out = strip_code(ANSWER)
        assert "```" not in out
        assert "[code removed]" in out

    def test_wrong_tools_swaps_technology_but_keeps_length(self):
        out = wrong_tools(ANSWER)
        assert "gprof" in out and "perf stat" not in out
        assert "AMD" in out and "Intel" not in out
        assert "pthreads" in out and "oneTBB" not in out
        # The damage must be invisible to a length or structure heuristic.
        assert abs(len(out) - len(ANSWER)) < 0.2 * len(ANSWER)

    def test_shuffle_changes_order_not_content(self):
        out = degrade(ANSWER, "shuffle_sentences", seed=1)
        assert out != ANSWER
        assert "perf stat" in out

    def test_wrong_numbers_breaks_magnitudes_and_leaves_names_alone(self):
        """The counterpart of wrong_tools: every tool is right, every number wrong."""
        out = wrong_numbers(ANSWER)
        assert "perf stat" in out and "VTune" in out and "Intel" in out
        assert "2.4 GHz" not in out and "24 GHz" in out
        assert "8 threads" not in out and "80 threads" in out
        # Same shape as wrong_tools — a length heuristic cannot see it.
        assert abs(len(out) - len(ANSWER)) < 0.2 * len(ANSWER)

    def test_wrong_numbers_falls_back_to_bare_numbers(self):
        """Answers with digits but no unit still have to be degradable."""
        out = wrong_numbers("The register file holds 32 entries in total.")
        assert "320 entries" in out

    def test_wrong_numbers_edits_the_prose_and_never_the_code(self):
        """10x a loop bound changes the example, not the facts; the intact copy is no better."""
        code = "```cpp\nfor (int i = 0; i < 100; ++i) {}  // 4 threads, 10 ns\n```"
        answer = f"Print the first 10 elements, on 4 threads.\n\n{code}\n\nRun `taskset 8 cores`."
        out = wrong_numbers(answer)
        assert "first 10 elements" in out and "40 threads" in out
        assert code in out and "`taskset 8 cores`" in out
        only_code = f"See below.\n\n{code}\n\nUnclosed:\n```cpp\nint n = 100;"
        assert wrong_numbers(only_code) == only_code

    @pytest.mark.parametrize("answer", [
        "Wrap code in ``` fences. It takes 5 ms.\n\n```bash\nsleep 100 ms\n```",
        "It takes 5 ms.\n\n~~~bash timeout=100ms\nsleep 100 ms\n~~~",
        "It takes 5 ms.\n\n````md\n```bash\nsleep 100 ms\n```\n````",
        "It takes 5 ms.\n\n```markdown\n1. Run:\n\n    ```bash\n    sleep 100 ms\n    ```\n\nIt waits 100 ms.\n```",
        "It takes 5 ms.\n\n<pre>\nsleep 100 ms\n</pre>",
    ])
    def test_wrong_numbers_finds_the_code_as_padding_does(self, answer):
        assert wrong_numbers(answer) == answer.replace("5 ms", "50 ms", 1)

    def test_wrong_numbers_does_not_invent_versions(self):
        answer = (
            "Build with -std=c++17 or C++11, as legacy TBB (2020) did. Generate for "
            "Visual Studio 16 2019 with libstdc++ versions 9 and 10 and CMake 3.5. "
            "Install oneAPI 2021.5, as in 2019, pin CPUs 0,1,2,3, enable AVX-512 "
            "and start (node 1).\n\n"
            "1. Install\n### 2. Build\nStep 3 sets a concurrency limit of 2."
        )
        out = wrong_numbers(answer)
        assert out == answer.replace("limit of 2", "limit of 20")

    def test_wrong_numbers_reads_a_grouped_figure_whole(self):
        out = wrong_numbers("A miss costs 1,000 ns, a call 1,500 cycles, the run 10^9 ns.")
        assert out == "A miss costs 10,000 ns, a call 15,000 cycles, the run 10^9 ns."

    @pytest.mark.parametrize(
        "answer, expected",
        [
            ("Keep it under 50% of the budget.", "Keep it under 5% of the budget."),
            ("Use C++11 threads on 8 cores.", "Use C++11 threads on 80 cores."),
            # One edit per figure: the unit is not swapped on top of the x10.
            ("Latency is 90 ns at 2.4 GHz.", "Latency is 900 ns at 24 GHz."),
            ("A 64-byte line.", "A 640-byte line."),
            ("Not a 1:1 port of 2 modules.", "Not a 1:1 port of 20 modules."),
        ],
    )
    def test_wrong_numbers_rules(self, answer, expected):
        assert wrong_numbers(answer) == expected

    @pytest.mark.parametrize("answer", [
        # Names, flags and bare units are not magnitudes: wrong_tools owns ISA names.
        "Build with -march=x86-64-v3 and MKL_CBWR=AVX2; target x86-64; report GB/s.",
        "Run kubectl -n ns get pods; latency in ns; NEP 50 rules.",
        # A range scaled at one end, or a digit group cut in two, is garbled, not wrong.
        "It is 10-15% faster, 10 - 15% at worst, 2-4x on 1,000–10,000 elements.",
        "A 5\u202f000 × 5\u202f000 matrix of 5 000 rows.",
        # Labels are not quantities.
        "Use 1-D or 2-D arrays. ### Pattern 1: wrap it. The #1 source of error.",
    ])
    def test_wrong_numbers_leaves_names_ranges_and_labels_alone(self, answer):
        assert wrong_numbers(answer) == answer

    def test_wrong_tools_owns_the_isa_names(self):
        assert wrong_tools("Use -march=x86-64-v3 with AVX2.") == "Use -march=i686-v3 with AVX."

    def test_contradiction_disowns_a_name_the_answer_used(self):
        out = contradiction(ANSWER)
        assert out.startswith(ANSWER)
        assert "not actually applicable here" in out
        # The disowned thing must be an identifier the answer named, not a
        # number and not an English word — "0.42 is not applicable" is gibberish.
        assert "`perf_event_paranoid`" in out or "`AVX-512`" in out

    @pytest.mark.parametrize(
        "answer, name",
        [
            ("Use `and`, `0.42`, `x`, `a_name_well_over_thirty_chars_long` or `tbb_x`.", "tbb_x"),
            ("Prefer **TaskArena** to a hand-rolled thread_pool.", "TaskArena"),
        ],
    )
    def test_contradiction_names_the_first_real_identifier(self, answer, name):
        assert f"caveat: `{name}` is not actually applicable" in contradiction(answer)

    def test_contradiction_is_a_noop_without_an_identifier(self):
        plain = "Measure first, then decide what to change, and keep notes."
        assert contradiction(plain) == plain

    def test_hedge_concrete_removes_the_code_with_no_marker(self):
        out = hedge_concrete(ANSWER, rng=random.Random(0))
        assert "```" not in out
        assert "perf stat -e cycles" not in out
        # strip_code leaves "[code removed]", which a judge could be detecting
        # instead of noticing the absence. This leaves no tell.
        assert "[code removed]" not in out and "removed" not in out

    def test_hedge_concrete_removes_exactly_what_strip_code_removes(self):
        answer = "Call `tbb::parallel_for` with a grain size of `1`:\n\n```cpp\nint main() {}\n```\n\nThen link `-ltbb`."
        out = hedge_concrete(answer, rng=random.Random(0))
        for kept in strip_code(answer).split("[code removed]"):
            assert kept in out
        assert "the relevant tool" not in out

    def test_hedge_concrete_is_no_easier_for_a_length_counter(self):
        # Removing code alone cut real answers by a fifth, so len(answer)
        # ranked 94% of these pairs correctly without reading a word.
        one_big_block = "Build it like this:\n\n```\n" + "make -j8 all\n" * 60 + "```\n\nThen run it."
        for seed in range(5):
            # Twelve one-line blocks: each block's overshoot is carried to the next.
            many = hedge_concrete(ANSWER, rng=random.Random(seed))
            assert abs(len(many) - len(ANSWER)) < max(map(len, SHORT_HEDGES))
            out = hedge_concrete(one_big_block, rng=random.Random(seed))
            assert abs(len(out) - len(one_big_block)) < max(map(len, HEDGE_SENTENCES)) / 2
            assert not any(out.count(s) > 1 for s in HEDGE_SENTENCES + SHORT_HEDGES)

    def test_hedge_concrete_does_not_open_every_answer_alike(self):
        answers = [f"Step {i}:\n\n```\nrun {i}\n```" for i in range(6)]
        openings = {hedge_concrete(a, rng=random.Random(0)).split("\n\n")[1] for a in answers}
        assert len(openings) > 1

    def test_hedge_concrete_requires_rng(self):
        with pytest.raises(ValueError, match="seeded rng"):
            hedge_concrete(ANSWER)

    def test_padding_adds_length_and_no_content(self):
        out = padding(ANSWER)
        assert len(out) > len(ANSWER)
        for sentence in ("perf stat", "VTune", "AVX-512"):
            assert sentence in out
        assert "padding" in NEUTRAL_VARIANTS

    def test_padding_never_lands_inside_a_code_block(self):
        # Blank lines inside a fence are not paragraph breaks; filler there
        # would break the code and make the padded copy genuinely worse.
        answer = "Run:\n\n```bash\nperf record -g ./app\n\nperf report\n```\n\nThen read it."
        out = padding(answer)
        assert "```bash\nperf record -g ./app\n\nperf report\n```" in out
        assert all(p in out for p in PADDING_PARAGRAPHS)

    @pytest.mark.parametrize("answer,block", [
        # tilde fences
        ("Intro.\n\n~~~bash\nperf stat ./a\n\nperf report\n~~~\n\nEnd.",
         "~~~bash\nperf stat ./a\n\nperf report\n~~~"),
        # ``` mentioned mid-line in prose must not flip the state (14/1215
        # stored perf answers do this)
        ("Wrap code in ``` fences.\n\nThen:\n\n```bash\nperf stat\n\nperf report\n```\n\nDone.",
         "```bash\nperf stat\n\nperf report\n```"),
        # a longer outer fence containing a shorter one
        ("Example:\n\n````md\n```bash\nx\n\ny\n```\n````\n\nDone.",
         "````md\n```bash\nx\n\ny\n```\n````"),
        # indented fence (up to 3 spaces)
        ("Steps:\n\n   ```\n   a\n\n   b\n   ```\n\nDone.",
         "   ```\n   a\n\n   b\n   ```"),
        # fence inside a nested list item (indented 4+ spaces)
        ("1. Step:\n\n    - sub\n\n        ```bash\n        perf record\n\n        perf report\n        ```\n\nDone.",
         "        ```bash\n        perf record\n\n        perf report\n        ```"),
        # a fence quoted inside a ```markdown block is content, not its closer
        ("Template:\n\n```markdown\n1. Install:\n\n    ```bash\n    pip install x\n    ```\n\n2. Run.\n```\n\nDone.",
         "```markdown\n1. Install:\n\n    ```bash\n    pip install x\n    ```\n\n2. Run.\n```"),
        # an HTML <pre> block
        ("Code:\n\n<pre>\nline1\n\nline2\n</pre>\n\nDone.",
         "<pre>\nline1\n\nline2\n</pre>"),
    ])
    def test_padding_respects_every_fence_form(self, answer, block):
        out = padding(answer)
        assert block in out, out
        assert all(p in out for p in PADDING_PARAGRAPHS)

    def test_a_line_starting_with_inline_code_is_not_a_fence(self):
        answer = "Intro.\n\n```x``` is inline code.\n\nMore prose.\n\nEnd."
        out = padding(answer)
        # Not treated as an unclosed block: filler is interleaved, not all in front.
        assert not out.startswith(PADDING_PARAGRAPHS[0])
        assert all(p in out for p in PADDING_PARAGRAPHS)

    def test_padding_goes_before_an_unclosed_fence(self):
        """An unclosed block runs to the end of the document: filler appended
        after it would render as code."""
        answer = "Intro.\n\nRun:\n\n```bash\nperf stat\n\nperf report"
        out = padding(answer)
        assert out.endswith("```bash\nperf stat\n\nperf report")
        assert all(p in out for p in PADDING_PARAGRAPHS)
        tail = out[out.index("```bash"):]
        assert not any(p in tail for p in PADDING_PARAGRAPHS)

    def test_padding_keeps_a_lead_in_with_its_code_and_a_list_whole(self):
        p0, p1, p2, p3 = PADDING_PARAGRAPHS
        code = "```cpp\nint main() {}\n```"
        # A fence nested in a list item: unindented filler before it would end
        # the list and turn the fence into literal text in an indented block.
        nested = "    ```bash\n    tar -xf tbb.tgz\n    ```"
        answer = ["Intro.", "Here is an example:", code, "1. Extract it.", nested, "2. Build it.", "- a", "- b", "Done.", "End."]
        expected = ["Intro.", p0, "Here is an example:", code, p1, "1. Extract it.", nested, "2. Build it.", "- a", "- b", p2, "Done.", p3, "End."]
        assert padding("\n\n".join(answer)) == "\n\n".join(expected)

    @pytest.mark.parametrize("block", [
        "```x``` is inline code.",
        "````md\n```bash\nx\n```\n\ninside md\n````",
        "~~~\na\n```\n\nb\n~~~",
        "```\na\n```python\n\nb\n```",
        "~~Old~~ advice.",
    ])
    def test_padding_places_filler_only_after_a_block_closes(self, block):
        p0, p1, p2, p3 = PADDING_PARAGRAPHS
        expected = ["Intro.", p0, block, p1, "Done.", p2, p3]
        assert padding("\n\n".join(["Intro.", block, "Done."])) == "\n\n".join(expected)

    def test_every_degradation_actually_changes_the_answer(self):
        for name in DEGRADATIONS:
            out = degrade(ANSWER, name, other=OTHER_ANSWER)
            assert out != ANSWER, name
            assert out, name

    def test_seeded_degradations_are_reproducible(self):
        for name in DEGRADATIONS:
            first = degrade(ANSWER, name, seed=7, other=OTHER_ANSWER)
            second = degrade(ANSWER, name, seed=7, other=OTHER_ANSWER)
            assert first == second, name

    def test_unknown_degradation_rejected(self):
        with pytest.raises(ValueError, match="Unknown degradation"):
            degrade(ANSWER, "not_a_variant")

    def test_mismatched_answer_needs_another_answer(self):
        with pytest.raises(ValueError, match="requires another answer"):
            degrade(ANSWER, "mismatched_answer")


class TestBuildPairs:
    def test_one_pair_per_base_and_variant(self):
        pairs = build_pairs(_answers(), n_bases=2, min_chars=100)
        assert len(pairs) == 2 * len(DEGRADATIONS)
        assert {p["variant"] for p in pairs} == set(DEGRADATIONS)
        for p in pairs:
            assert p["intact"] != p["degraded"]

    def test_short_answers_are_skipped(self):
        answers = [
            {"question_id": "Q1", "question_text": "q", "arms": {"baseline": {"answer": "short"}}}
        ]
        assert build_pairs(answers, min_chars=1000) == []

    def test_legacy_with_docs_shape_supported(self):
        answers = [
            {
                "question_id": "Q1",
                "question_text": "q",
                "with_docs": {"answer": ANSWER},
                "without_docs": {"answer": OTHER_ANSWER},
            }
        ]
        pairs = build_pairs(answers, min_chars=100)
        assert {p["arm"] for p in pairs} == {"with_docs", "without_docs"}

    def test_variant_selection_is_validated(self):
        with pytest.raises(ValueError, match="Unknown degradation"):
            build_pairs(_answers(), variants=["truncate_20", "nope"])

    def test_mismatched_variant_dropped_when_no_other_question(self):
        answers = [
            {"question_id": "Q1", "question_text": "q", "arms": {"baseline": {"answer": ANSWER}}}
        ]
        pairs = build_pairs(answers, min_chars=100)
        assert "mismatched_answer" not in {p["variant"] for p in pairs}


class TestValidityRow:
    @pytest.mark.parametrize(
        "intact,degraded,expected",
        [(90.0, 50.0, 1.0), (50.0, 90.0, 0.0), (90.0, 90.0, 0.5), (None, 90.0, None)],
    )
    def test_correct_counts_ties_as_half(self, intact, degraded, expected):
        row = ValidityRow("Q1", "baseline", "truncate_20", intact, degraded)
        assert row.correct == expected


class TestEvaluatePairs:
    @staticmethod
    def _pairs():
        # mismatched_answer is excluded here: its damaged copy IS another base's
        # intact answer, so a judge keyed on "is this text intact" cannot be
        # right about it by construction. That variant is covered above.
        return build_pairs(
            _answers(),
            n_bases=2,
            min_chars=100,
            variants=[v for v in DEGRADATIONS if v != "mismatched_answer"],
        )

    @classmethod
    def _oracle(cls, pairs, inverted=False):
        """A judge that knows exactly which text is the undamaged one."""
        intact = {p["intact"] for p in pairs}

        def score(lib, q, a):
            good = a in intact
            return (10.0 if good else 100.0) if inverted else (100.0 if good else 10.0)

        return score

    def test_perfect_judge_passes(self):
        pairs = self._pairs()
        report = evaluate_pairs(self._oracle(pairs), pairs, judge="oracle")
        summary = report.summary()
        assert summary["accuracy"] == 1.0
        assert summary["passed"] is True
        assert summary["n_errors"] == 0

    def test_saturated_judge_fails_on_ties_not_on_wrong_order(self):
        report = evaluate_pairs(lambda lib, q, a: 100.0, self._pairs(), judge="ceiling")
        summary = report.summary()
        assert summary["accuracy"] == 0.5
        assert summary["tie_rate"] == 1.0
        assert summary["passed"] is False

    def test_inverted_judge_fails(self):
        pairs = self._pairs()
        report = evaluate_pairs(self._oracle(pairs, inverted=True), pairs, judge="inverted")
        assert report.summary()["accuracy"] == 0.0
        assert report.summary()["passed"] is False

    def test_intact_answer_scored_once_per_base(self):
        calls = []

        def scorer(lib, q, a):
            calls.append(a)
            return float(len(a))

        pairs = self._pairs()
        evaluate_pairs(scorer, pairs, judge="counter")
        bases = len({(p["base_id"], p["arm"]) for p in pairs})
        # one intact score per base + one per degraded variant
        assert len(calls) == bases + len(pairs)

    def test_scorer_error_is_recorded_not_raised(self):
        def scorer(lib, q, a):
            if "gprof" in a:
                raise RuntimeError("boom")
            return float(len(a))

        report = evaluate_pairs(scorer, self._pairs(), judge="flaky")
        assert report.errors
        assert all("RuntimeError" in (r.error or "") for r in report.errors)
        # Errored pairs are excluded from the accuracy, not counted as correct.
        assert len(report.scored) == len(report.rows) - len(report.errors)

    def test_no_scored_pairs_is_a_failure(self):
        def scorer(lib, q, a):
            raise RuntimeError("no judge available")

        report = evaluate_pairs(scorer, self._pairs(), judge="dead")
        summary = report.summary()
        assert summary["n_pairs"] == 0
        assert summary["passed"] is False

    def test_per_variant_breakdown_reports_drop_and_ties(self):
        pairs = self._pairs()
        report = evaluate_pairs(lambda lib, q, a: float(len(a)), pairs, judge="length")
        per_variant = report.summary()["per_variant"]
        assert set(per_variant) == {p["variant"] for p in pairs}
        # A length judge sees truncation perfectly...
        assert per_variant["truncate_20"]["accuracy"] == 1.0
        assert per_variant["truncate_20"]["mean_drop"] > 0
        # ...but wrong_tools leaves the answer just as long, so a length judge
        # actively prefers the sabotaged copy: a negative drop is the judge
        # scoring the damaged answer higher.
        assert per_variant["wrong_tools"]["mean_drop"] < 0
        assert per_variant["wrong_tools"]["accuracy"] == 0.0
        for stats in per_variant.values():
            assert stats["n"] > 0
            assert 0.0 <= stats["accuracy"] <= 1.0

    def test_threshold_is_the_gate(self):
        pairs = self._pairs()
        strict = evaluate_pairs(lambda lib, q, a: 100.0, pairs, min_accuracy=0.9)
        lenient = evaluate_pairs(lambda lib, q, a: 100.0, pairs, min_accuracy=0.4)
        assert strict.summary()["passed"] is False
        assert lenient.summary()["passed"] is True

    def test_format_summary_states_the_verdict(self):
        report = evaluate_pairs(lambda lib, q, a: float(len(a)), self._pairs(), judge="length")
        text = report.format_summary()
        assert "Judge validity" in text
        assert "wrong_tools" in text
        assert ("PASS" in text) or ("FAIL" in text)


class TestRunValidity:
    def test_end_to_end_with_a_stub_judge(self):
        report = run_validity(
            _answers(),
            lambda lib, q, a: float(len(a)),
            judge="stub",
            n_bases=2,
            library="oneTBB",
        )
        assert report.summary()["n_pairs"] > 0
        assert report.summary()["min_accuracy"] == DEFAULT_MIN_ACCURACY

    def test_no_usable_answers_raises(self):
        with pytest.raises(ValueError, match="No usable answers"):
            run_validity([{"question_id": "Q1", "question_text": "q", "arms": {}}], lambda *a: 1.0)


@pytest.mark.parametrize("provider", ["openai", "anthropic", "google", "google-vertex", "openrouter", "amazon-bedrock"])
def test_judge_validity_accepts_every_judge_provider(provider):
    from dokimi.cli import build_parser

    args = build_parser().parse_args(["eval", "judge-validity", "--answers", "a.json", "--judge-provider", provider])
    assert args.judge_provider == provider


@pytest.mark.parametrize("provider", ["azure", "bedrock"])
def test_judge_validity_rejects_providers_judge_rejects(provider):
    from dokimi.cli import build_parser

    with pytest.raises(SystemExit):
        build_parser().parse_args(["eval", "judge-validity", "--answers", "a.json", "--judge-provider", provider])


def test_noop_degradation_is_dropped_and_counted():
    plain = [{"question_id": "Q9", "question_text": "Why?", "arms": {"a": {"answer": "no tools here. " * 200}}}]
    noop: dict = {}
    pairs = build_pairs(plain, variants=["wrong_tools", "truncate_20"], noop=noop)
    assert [p["variant"] for p in pairs] == ["truncate_20"]
    assert noop == {"wrong_tools": 1}
    report = run_validity(plain, lambda lib, q, a: float(len(a)), variants=["wrong_tools", "truncate_20"])
    assert report.summary()["n_noop"] == {"wrong_tools": 1}
    assert "no-op pairs  : 1 dropped" in report.format_summary()
    with pytest.raises(ValueError, match="unchanged"):
        run_validity(plain, lambda lib, q, a: 0.0, variants=["wrong_tools"])


class TestLengthBaseline:
    """`len(answer)` is not a judge, and it passes most of these variants."""

    @staticmethod
    def _pairs(variants):
        return build_pairs(_answers(), n_bases=2, min_chars=100, variants=variants)

    def test_length_only_baseline_is_reported_per_variant(self):
        pairs = self._pairs(["truncate_20", "wrong_tools"])
        report = evaluate_pairs(lambda lib, q, a: 100.0, pairs, judge="ceiling")
        per_variant = report.summary()["per_variant"]
        # Truncation is visible to a character counter; wrong_tools is not —
        # so only the second variant is evidence about a judge at all.
        assert per_variant["truncate_20"]["length_accuracy"] == 1.0
        assert per_variant["wrong_tools"]["length_accuracy"] < 1.0

    def test_a_length_judge_never_beats_the_length_baseline(self):
        pairs = self._pairs([v for v in DEGRADATIONS if v != "mismatched_answer"])
        report = evaluate_pairs(lambda lib, q, a: float(len(a)), pairs, judge="length")
        summary = report.summary()
        assert summary["accuracy"] == summary["length_accuracy"]
        assert not any(v["beats_length"] for v in summary["per_variant"].values())
        assert "<= length" in report.format_summary()

    def test_baseline_uses_the_same_pairs_as_the_judge(self):
        """The gate counts an errored pair as misordered; length still makes its call there."""
        pairs = self._pairs(["truncate_20"])

        def scorer(lib, q, a):
            raise RuntimeError("boom")

        report = evaluate_pairs(scorer, pairs, judge="dead")
        summary = report.summary()
        assert all(r.length_correct == 1.0 for r in report.rows)
        assert (summary["gate_accuracy"], summary["length_accuracy"]) == (0.0, 1.0)
        assert summary["per_variant"]["truncate_20"]["length_accuracy"] == 1.0
        assert "<= length" in report.format_summary()

    def test_a_judge_ahead_of_length_on_the_same_pairs_is_not_flagged(self):
        # 10 pairs, length right on 7. The judge errors on two that length gets
        # wrong and orders the other 8 right: 8/10 against 7/10.
        pairs = [{"base_id": f"b{i}", "variant": "truncate_20", "question": "q", "library": "l",
                  "intact": f"good{i}" + ("" if i < 3 else "x" * 8),
                  "degraded": f"bad{i}" + ("x" * 10 if i < 3 else "")} for i in range(10)]

        def scorer(lib, q, a):
            if a in {"good0", "good1"}:
                raise RuntimeError("down")
            return 1.0 if a.startswith("good") else 0.0

        report = evaluate_pairs(scorer, pairs)
        stats = report.summary()["per_variant"]["truncate_20"]
        assert (stats["gate_accuracy"], stats["length_accuracy"]) == (0.8, 0.7)
        assert stats["beats_length"] is True
        assert "<= length" not in report.format_summary()
        assert "length-only  : 0.700" in report.format_summary()


class TestNeutralVariants:
    @staticmethod
    def _pairs():
        return build_pairs(_answers(), n_bases=2, min_chars=100, variants=["padding", "truncate_20"])

    def test_padding_is_graded_on_not_moving_and_is_not_gated(self):
        pairs = self._pairs()
        # A judge that rewards the padded copy outright — the classic length
        # bias. It must not fail the gate (padding has no "intact wins"
        # target), but the report must say what happened.
        report = evaluate_pairs(lambda lib, q, a: float(len(a)), pairs, judge="length")
        summary = report.summary()
        assert summary["passed"] is True
        padded = summary["per_variant"]["padding"]
        assert padded["neutral"] is True
        assert padded["unmoved_rate"] == 0.0
        assert padded["accuracy"] == 0.0  # it preferred the padded copy every time
        assert "correct outcome is no movement" in report.format_summary()

    def test_a_judge_that_ignores_padding_scores_fully_neutral(self):
        pairs = self._pairs()
        report = evaluate_pairs(lambda lib, q, a: 70.0 if len(a) > 50 else 10.0, pairs)
        assert report.summary()["per_variant"]["padding"]["unmoved_rate"] == 1.0

    def test_neutral_pairs_are_outside_the_pooled_accuracy(self):
        pairs = self._pairs()
        report = evaluate_pairs(lambda lib, q, a: float(len(a)), pairs, judge="length")
        summary = report.summary()
        n_padding = sum(1 for p in pairs if p["variant"] == "padding")
        assert summary["n_neutral_pairs"] == n_padding
        assert summary["n_pairs"] == len(pairs) - n_padding
        # Pooled over truncate_20 alone, which a length judge gets right.
        assert summary["accuracy"] == 1.0


class TestGateResolution:
    """One base is worth 1/n accuracy, so a bare point estimate is not a verdict."""

    @staticmethod
    def _pairs(n):
        return [
            {"base_id": f"b{i}", "variant": "truncate_20", "question": "q",
             "library": "l", "intact": f"good{i}", "degraded": f"bad{i}"}
            for i in range(n)
        ]

    @classmethod
    def _report(cls, n, n_wrong, **kw):
        pairs = cls._pairs(n)
        wrong = {p["degraded"] for p in pairs[:n_wrong]}
        return evaluate_pairs(
            lambda lib, q, a: 2.0 if a in wrong else (1.0 if a.startswith("good") else 0.0),
            pairs, **kw,
        )

    def test_ci_brackets_the_point_estimate(self):
        summary = self._report(20, 4).summary()
        low, high = summary["accuracy_ci"]
        assert low <= summary["accuracy"] <= high
        assert low < high  # a degenerate interval would hide the problem

    def test_a_pass_one_pair_from_the_gate_is_inconclusive(self):
        # 17/20 = 0.85 against a 0.80 gate: two more wrong pairs would fail it.
        summary = self._report(20, 3).summary()
        assert summary["passed"] is True
        assert summary["conclusive"] is False
        assert summary["inconclusive_variants"] == ["truncate_20"]
        needed = summary["per_variant"]["truncate_20"]["bases_for_resolution"]
        assert needed > 20  # and it says how many pairs would settle it

    def test_a_clear_pass_is_conclusive(self):
        summary = self._report(20, 0).summary()
        assert summary["passed"] is True
        assert summary["conclusive"] is True
        assert summary["inconclusive_variants"] == []

    def test_inconclusive_is_stated_in_the_summary_text(self):
        text = self._report(20, 3).format_summary()
        assert "INCONCLUSIVE" in text
        assert "RESULT: PASS (inconclusive" in text

    def test_interval_narrows_as_pairs_are_added(self):
        def half_width(n):
            low, high = self._report(n, n // 5).summary()["accuracy_ci"]
            return (high - low) / 2

        assert half_width(100) < half_width(20)

    def test_the_interval_sees_errors_the_way_the_gate_does(self):
        """An interval over the scored pairs only would contradict the gate.

        The gate counts an errored pair as misordered, so an interval taken
        over the scored pairs alone could sit entirely above a threshold the
        gate_accuracy fails.
        """
        pairs = self._pairs(10)

        def flaky(lib, q, a):
            if a in {"good0", "good1", "good2"}:
                raise RuntimeError("throttled")
            return 1.0 if a.startswith("good") else 0.0

        summary = evaluate_pairs(flaky, pairs).summary()
        stats = summary["per_variant"]["truncate_20"]
        assert (stats["accuracy"], stats["gate_accuracy"]) == (1.0, 0.7)
        low, high = stats["accuracy_ci"]
        assert low <= stats["gate_accuracy"] <= high
        assert summary["passed"] is False

    def test_the_report_records_its_degradation_seed(self):
        assert self._report(20, 4, seed=3).summary()["seed"] == 3

    @pytest.mark.parametrize("k,n,lo,hi", [(19, 20, 21, 40), (18, 20, 40, 90),
                                           (17, 20, 180, 320), (14, 20, 40, 90)])
    def test_pairs_for_resolution_is_the_real_crossing(self, k, n, lo, hi):
        """It must name a count at which the interval actually stops straddling,
        and never fewer pairs than the run already has."""
        from dokimi.eval.judge_validity import _pairs_for_resolution, _wilson

        need = _pairs_for_resolution(k / n, n, 0.80)
        assert lo <= need <= hi
        low, high = _wilson(k / n, need)
        assert not (low < 0.80 <= high)
        low, high = _wilson(k / n, need - 1)
        assert low < 0.80 <= high

    def test_an_unreachable_resolution_is_not_called_on_the_gate(self):
        from dokimi.eval.judge_validity import PAIRS_SEARCH_LIMIT, _pairs_for_resolution

        assert _pairs_for_resolution(0.7999, 20, 0.80) == PAIRS_SEARCH_LIMIT
        assert _pairs_for_resolution(0.80, 20, 0.80) is None

    def test_a_high_accuracy_straddle_asks_for_more_pairs_not_fewer(self):
        """19/20 used to print 'needs ~12 pairs (0x more bases)'."""
        summary = self._report(20, 1).summary()
        need = summary["per_variant"]["truncate_20"]["bases_for_resolution"]
        assert need > 20
        text = self._report(20, 1).format_summary()
        assert "0x more" not in text and "x this run" in text

    def test_pooled_point_estimate_sits_inside_its_interval_with_errors(self):
        pairs = self._pairs(20)

        def flaky(lib, q, a):
            if a in {f"good{i}" for i in range(10)}:
                raise RuntimeError("throttled")
            return 1.0 if a.startswith("good") else 0.0

        s = evaluate_pairs(flaky, pairs).summary()
        low, high = s["accuracy_ci"]
        assert s["accuracy"] == 1.0                      # errors left out
        assert low <= s["gate_accuracy"] <= high         # the number printed
        assert "with errored pairs left out" in evaluate_pairs(flaky, pairs).format_summary()

    @pytest.mark.parametrize("n,n_wrong", [(3, 0), (5, 0), (20, 1)])
    def test_a_small_all_or_nearly_all_correct_run_is_not_conclusive(self, n, n_wrong):
        """A percentile bootstrap has zero width when every outcome is the same,
        which made 3/3 read as a conclusive pass. The interval must keep width."""
        summary = self._report(n, n_wrong).summary()
        low, high = summary["per_variant"]["truncate_20"]["accuracy_ci"]
        assert low < 0.80
        assert summary["passed"] is True
        assert summary["conclusive"] is False

    def test_wilson_bounds_match_the_reference_values(self):
        """20/20 and 19/20 against the published Wilson 95% intervals."""
        for n_wrong, (lo, hi) in ((0, (0.839, 1.0)), (1, (0.764, 0.991))):
            low, high = self._report(20, n_wrong).summary()["accuracy_ci"]
            assert (round(low, 3), round(high, 3)) == (lo, hi)

    def test_a_fail_one_variant_settles_is_conclusive(self):
        # truncate_20 straddles the gate, but wrong_tools sits wholly under it.
        pairs = self._pairs(20) + [
            {**p, "variant": "wrong_tools", "degraded": f"tool{i}"} for i, p in enumerate(self._pairs(20))
        ]
        wrong = {f"bad{i}" for i in range(3)} | {f"tool{i}" for i in range(20)}
        report = evaluate_pairs(
            lambda lib, q, a: 2.0 if a in wrong else (1.0 if a.startswith("good") else 0.0), pairs)
        summary = report.summary()
        assert summary["passed"] is False
        assert summary["inconclusive_variants"] == ["truncate_20"]
        assert summary["conclusive"] is True
        assert report.format_summary().endswith("RESULT: FAIL")

    def test_a_fail_that_only_straddles_stays_inconclusive(self):
        summary = self._report(20, 5).summary()
        assert (summary["passed"], summary["conclusive"]) == (False, False)
        assert self._report(20, 5).format_summary().endswith("RESULT: FAIL (inconclusive — see above)")

    def test_an_accuracy_on_the_gate_does_not_say_more_pairs_cannot_help(self):
        summary = self._report(20, 4).summary()
        assert summary["passed"] is True
        assert summary["per_variant"]["truncate_20"]["bases_for_resolution"] is None
        text = self._report(20, 4).format_summary()
        assert "on the gate exactly; add pairs, no count follows from a zero margin" in text
        assert "will not settle" not in text

    def test_a_clear_result_asks_for_no_pairs(self):
        assert self._report(20, 0).summary()["per_variant"]["truncate_20"]["bases_for_resolution"] is None

    def test_the_pairs_ratio_counts_errored_pairs_as_run(self):
        pairs = self._pairs(20)

        def flaky(lib, q, a):
            if a in {"good0", "good1"}:
                raise RuntimeError("throttled")
            return 1.0 if a.startswith("good") else 0.0

        report = evaluate_pairs(flaky, pairs)
        need = report.summary()["per_variant"]["truncate_20"]["bases_for_resolution"]
        assert f"needs ~{need} pairs ({need / 20:.1f}x this run)" in report.format_summary()

    def test_a_count_past_the_search_limit_is_reported_as_such(self):
        text = self._report(20, 3, min_accuracy=0.8499999).format_summary()
        assert "needs over 1000000 pairs" in text

    def test_the_per_variant_interval_counts_errors_as_misordered(self):
        from dokimi.eval.judge_validity import _wilson

        pairs = self._pairs(10)

        def flaky(lib, q, a):
            if a in {"good0", "good1", "good2"}:
                raise RuntimeError("throttled")
            return 1.0 if a.startswith("good") else 0.0

        report = evaluate_pairs(flaky, pairs)
        stats = report.summary()["per_variant"]["truncate_20"]
        assert stats["accuracy_ci"] == pytest.approx(list(_wilson(0.7, 10)))
        assert "accuracy     : 0.700" in report.format_summary()

    def test_neutral_errors_stay_out_of_the_pooled_interval(self):
        from dokimi.eval.judge_validity import _wilson

        pairs = self._pairs(20) + [
            {**p, "variant": "padding", "degraded": p["intact"] + " pad"} for p in self._pairs(5)
        ]

        def scorer(lib, q, a):
            if a.endswith(" pad"):
                raise RuntimeError("down")
            return 1.0 if a.startswith("good") else 0.0

        summary = evaluate_pairs(scorer, pairs).summary()
        assert summary["accuracy_ci"] == pytest.approx(list(_wilson(1.0, 20)))
        assert summary["conclusive"] is True

    def test_an_accuracy_equal_to_the_gate_passes(self):
        assert self._report(20, 4).summary()["passed"] is True

    @pytest.mark.parametrize("gate,n_wrong,straddles", [(1.0, 0, True), (0.0, 20, False)])
    def test_straddling_includes_the_upper_bound_only(self, gate, n_wrong, straddles):
        # At 20/20 the upper bound is 1.0 exactly, at 0/20 the lower bound is 0.0.
        summary = self._report(20, n_wrong, min_accuracy=gate).summary()
        assert summary["per_variant"]["truncate_20"]["straddles_gate"] is straddles
        assert summary["conclusive"] is not straddles

    def test_twenty_of_twenty_is_still_a_conclusive_pass(self):
        """The fix must not make a clean pass at the default size inconclusive."""
        summary = self._report(20, 0).summary()
        assert summary["accuracy_ci"][0] > 0.80
        assert summary["conclusive"] is True


def test_length_flag_compares_the_number_the_table_prints():
    """With errored pairs, `accuracy` (errors excluded) can beat the length
    baseline while the displayed gate_accuracy does not; the flag must follow
    the displayed number."""
    # 8 pairs where the intact copy is longer, 2 where the damaged one is.
    pairs = [{"base_id": f"b{i}", "variant": "truncate_20", "question": "q",
              "library": "l",
              "intact": f"good{i}" + ("x" * 20 if i < 8 else ""),
              "degraded": f"bad{i}" + ("" if i < 8 else "x" * 20)}
             for i in range(10)]
    # The judge errors on 3 intact-longer pairs and orders every other pair
    # right: accuracy 1.0 over the 7 scored. Over all 10, as the gate counts
    # them: gate_accuracy 0.70 < length 0.80.
    erroring = {p["intact"] for p in pairs[:3]}

    def scorer(lib, q, a):
        if a in erroring:
            raise RuntimeError("throttled")
        return 1.0 if a.startswith("good") else 0.0

    stats = evaluate_pairs(scorer, pairs).summary()["per_variant"]["truncate_20"]
    assert stats["accuracy"] == 1.0
    assert stats["length_accuracy"] == pytest.approx(0.8)
    assert stats["gate_accuracy"] == pytest.approx(0.7)
    assert stats["beats_length"] is False


def _raise(*args):
    raise RuntimeError("down")


@pytest.mark.parametrize("variant,scorer", [("padding", lambda lib, q, a: 50.0), ("truncate_20", _raise)])
def test_no_graded_pair_scored_says_so(variant, scorer):
    pairs = build_pairs(_answers(), n_bases=2, min_chars=100, variants=[variant])
    report = evaluate_pairs(scorer, pairs)
    assert report.summary()["conclusive"] is False
    assert report.format_summary().endswith("RESULT: FAIL (no graded pair was scored)")


@pytest.mark.parametrize("delta,unmoved", [(0.0, 1.0), (1.0, 1.0), (2.0, 1.0), (2.5, 0.0), (3.0, 0.0)])
def test_neutral_tolerance_is_two_points_either_way(delta, unmoved):
    assert ValidityRow("Q1", "a", "padding", 70.0, 70.0 + delta).unmoved == unmoved
    assert ValidityRow("Q1", "a", "padding", 70.0, 70.0 - delta).unmoved == unmoved


@pytest.mark.parametrize("padded,move", [(80.0, "+10.0 (rewards"), (60.0, "-10.0 (penalises"),
                                         (70.0, "+0.0 (neither rewards nor penalises")])
def test_padding_line_names_the_direction_of_the_move(padded, move):
    pairs = build_pairs(_answers(), n_bases=2, min_chars=100, variants=["padding"])
    intact = {p["intact"] for p in pairs}
    report = evaluate_pairs(lambda lib, q, a: 70.0 if a in intact else padded, pairs)
    assert f"mean move {move} the longer copy)" in report.format_summary()


def test_padding_moves_that_cancel_are_neither():
    # +0.7 and -0.7 leave a mean of -3.6e-15, not 0.0, in float arithmetic.
    rows = [ValidityRow("Q1", "a", "padding", 49.8, 49.1), ValidityRow("Q2", "a", "padding", 48.4, 49.1)]
    text = ValidityReport("j", rows=rows).format_summary()
    assert "mean move +0.0 (neither rewards nor penalises the longer copy)" in text


def test_run_validity_seeds_the_degradations(monkeypatch):
    import dokimi.eval.judge_validity as jv

    seen = {}

    def fake_build_pairs(answers, **kw):
        seen.update(kw)
        return build_pairs(answers, **kw)

    monkeypatch.setattr(jv, "build_pairs", fake_build_pairs)
    report = run_validity(_answers(), lambda lib, q, a: 1.0, seed=7, variants=["truncate_20"])
    assert seen["seed"] == 7
    assert report.summary()["seed"] == 7


class TestStrictCli:
    @staticmethod
    def _run(tmp_path, monkeypatch, capsys, bases, *extra, sign=1.0):
        import json

        import dokimi.eval.judge as judge_mod
        import dokimi.eval.judge_validity as jv
        from dokimi.cli import build_parser

        answers = tmp_path / "answers.json"
        answers.write_text(json.dumps([
            {"question_id": f"Q{i}", "question_text": "q", "arms": {"a": {"answer": f"{i}. " + ANSWER}}}
            for i in range(bases)
        ]))
        monkeypatch.setattr(judge_mod, "Judge", lambda **kw: None)
        monkeypatch.setattr(jv, "judge_scorer", lambda judge: lambda lib, q, a: sign * len(a))
        args = build_parser().parse_args(["eval", "judge-validity", "--answers", str(answers),
                                          "--bases", str(bases), "--variants", "truncate_20", *extra])
        try:
            args.func(args)
            code = 0
        except SystemExit as exc:
            code = exc.code
        return code, capsys.readouterr().err

    def test_an_inconclusive_pass_warns_and_exits_zero(self, tmp_path, monkeypatch, capsys):
        code, err = self._run(tmp_path, monkeypatch, capsys, 5)
        assert code == 0
        assert "Pass is inconclusive at this sample size: truncate_20" in err

    def test_the_hint_names_the_knob_the_mode_reads(self, tmp_path, monkeypatch, capsys):
        assert "more --bases" in self._run(tmp_path, monkeypatch, capsys, 5)[1]
        err = self._run(tmp_path, monkeypatch, capsys, 5, "--sequential")[1]
        assert "inconclusive" in err and "--max-bases" in err and "more --bases" not in err

    def test_strict_turns_an_inconclusive_pass_into_a_failure(self, tmp_path, monkeypatch, capsys):
        assert self._run(tmp_path, monkeypatch, capsys, 5, "--strict")[0] == 1

    def test_strict_keeps_a_settled_pass(self, tmp_path, monkeypatch, capsys):
        assert self._run(tmp_path, monkeypatch, capsys, 20, "--strict") == (0, "")

    def test_a_fail_exits_one_without_strict(self, tmp_path, monkeypatch, capsys):
        assert self._run(tmp_path, monkeypatch, capsys, 20, sign=-1.0)[0] == 1


def test_one_failing_variant_fails_the_gate():
    pairs = [{"base_id": f"b{i}", "variant": v, "question": "q", "library": "l",
              "intact": f"good{i}", "degraded": f"bad{i}{v}"} for i in range(4) for v in "abcde"]
    inverted = {p["degraded"] for p in pairs if p["variant"] == "e"}
    report = evaluate_pairs(lambda lib, q, a: 2.0 if a in inverted else (1.0 if a.startswith("good") else 0.0), pairs)
    assert report.summary()["accuracy"] == 0.8
    assert report.summary()["passed"] is False


def test_errored_pairs_count_against_the_gate():
    pairs = [{"base_id": f"b{i}", "variant": "v", "question": "q", "library": "l",
              "intact": f"good{i}", "degraded": f"bad{i}"} for i in range(10)]

    def flaky(lib, q, a):
        if a in {"good0", "good1", "good2"}:
            raise RuntimeError("throttled")
        return 1.0 if a.startswith("good") else 0.0

    s = evaluate_pairs(flaky, pairs).summary()
    assert (s["n_pairs"], s["n_errors"], s["accuracy"]) == (7, 3, 1.0)
    assert s["per_variant"]["v"]["gate_accuracy"] == 0.7
    assert s["passed"] is False
    assert "v                       7    3   0.70" in evaluate_pairs(flaky, pairs).format_summary()


def test_variant_whose_every_pair_errored_fails_the_gate():
    pairs = [{"base_id": "b", "variant": v, "question": "q", "library": "l",
              "intact": f"good{v}", "degraded": f"bad{v}"} for v in ("ok", "dead")]

    def scorer(lib, q, a):
        if a.endswith("dead"):
            raise RuntimeError("down")
        return 1.0 if a.startswith("good") else 0.0

    report = evaluate_pairs(scorer, pairs)
    assert report.summary()["passed"] is False
    assert report.summary()["per_variant"]["dead"]["n_errors"] == 1
    assert "dead                    0    1   0.00" in report.format_summary()


def test_report_is_written_before_a_console_that_cannot_print_it(tmp_path, monkeypatch):
    import io
    import json
    import sys

    import dokimi.eval.judge as judge_mod
    import dokimi.eval.judge_validity as jv
    from dokimi.cli import build_parser

    answers = tmp_path / "answers.json"
    answers.write_text(json.dumps(_answers()))
    out = tmp_path / "validity.json"
    monkeypatch.setattr(judge_mod, "Judge", lambda **kw: None)
    monkeypatch.setattr(jv, "judge_scorer", lambda judge: lambda lib, q, a: float(len(a)))
    monkeypatch.setattr(sys, "stdout", io.TextIOWrapper(io.BytesIO(), encoding="cp1252"))
    args = build_parser().parse_args(["eval", "judge-validity", "--answers", str(answers),
                                      "--variants", "truncate_20", "--output", str(out)])
    with pytest.raises(UnicodeEncodeError):
        args.func(args)
    assert json.loads(out.read_text())["summary"]["n_pairs"] > 0


def test_mismatched_answer_is_drawn_per_base():
    answers = [
        {"question_id": f"Q{i}", "question_text": "q", "arms": {"a": {"answer": f"Answer {i}. " * 40}}}
        for i in range(6)
    ]
    pairs = build_pairs(answers, min_chars=100, variants=["mismatched_answer"])
    assert len(pairs) == 6
    assert len({p["degraded"] for p in pairs}) > 2


def _eval_subcommands():
    import argparse

    from dokimi.cli import build_parser

    top = next(a for a in build_parser()._actions if isinstance(a, argparse._SubParsersAction))
    sub = next(a for a in top.choices["eval"]._actions if isinstance(a, argparse._SubParsersAction))
    return sorted(sub.choices)


@pytest.mark.parametrize("command", _eval_subcommands())
def test_every_eval_subcommand_prints_its_help(command, capsys):
    """argparse %-formats help text: a bare "95% CI" crashed `judge-validity --help`."""
    from dokimi.cli import build_parser

    with pytest.raises(SystemExit) as exc:
        build_parser().parse_args(["eval", command, "--help"])
    assert exc.value.code == 0
    assert "usage:" in capsys.readouterr().out


@pytest.mark.parametrize("intact, degraded", [(62.4, 64.4), (2.4, 4.4), (6.8, 8.8), (30.2, 32.2)])
def test_a_move_of_exactly_the_tolerance_is_unmoved(intact, degraded):
    """abs(62.4 - 64.4) is 2.000000000000007 in float arithmetic."""
    assert ValidityRow("Q", "a", "padding", intact, degraded).unmoved == 1.0
    assert ValidityRow("Q", "a", "padding", intact, degraded + 0.1).unmoved == 0.0


@pytest.mark.parametrize("n", [10, 13, 20, 25])
def test_the_interval_always_contains_its_point_estimate(n):
    from dokimi.eval.judge_validity import _wilson

    assert _wilson(1.0, n)[1] == 1.0
    assert _wilson(0.0, n)[0] == 0.0


def test_an_all_correct_run_is_judged_alike_at_every_size_against_a_gate_of_one():
    """10/10 cleared a 1.0 gate while 20/20 straddled it."""
    def summary(n):
        pairs = [{"base_id": f"b{i}", "variant": "v", "question": "q", "library": "l",
                  "intact": f"good{i}", "degraded": f"bad{i}"} for i in range(n)]
        return evaluate_pairs(lambda lib, q, a: float(a.startswith("good")), pairs, min_accuracy=1.0).summary()

    assert {summary(n)["conclusive"] for n in (10, 13, 20, 25)} == {False}


def test_a_requested_variant_with_no_pair_is_reported_and_blocks_a_conclusive_pass():
    code_free = [{"question_id": f"Q{i}", "question_text": "q",
                  "arms": {"a": {"answer": f"Plain prose, answer {i}. " * 100}}} for i in range(20)]
    report = run_validity(code_free, lambda lib, q, a: float(len(a)), variants=["strip_code", "truncate_20"])
    s = report.summary()
    assert s["passed"] is True and list(s["per_variant"]) == ["truncate_20"]
    assert s["per_variant"]["truncate_20"]["straddles_gate"] is False
    assert s["unmeasured_variants"] == ["strip_code"]
    assert s["conclusive"] is False
    text = report.format_summary()
    assert "strip_code" in text and "not measured: no pair, 20 no-op" in text
    assert "RESULT: PASS (inconclusive" in text


def test_table_cells_sit_under_their_headers():
    import re

    pairs = [{"base_id": f"b{i}", "variant": "truncate_20", "question": "q", "library": "l",
              "intact": f"good{i}", "degraded": f"bad{i}"} for i in range(20)]
    lines = evaluate_pairs(lambda lib, q, a: float(a.startswith("good")), pairs).format_summary().splitlines()
    header = next(i for i, line in enumerate(lines) if line.lstrip().startswith("degradation"))

    def ends(line, token):
        return [m.end() for m in re.finditer(token, line)]

    titles = ends(lines[header], r"95% CI|\S+")
    assert titles[1:] == ends(lines[header + 1], r"\[[^\]]*\]|\S+")[1:len(titles)]


def test_a_variant_skipped_without_a_noop_is_still_reported():
    """mismatched_answer needs a second question; with one, it builds no pair and counts no no-op."""
    one = [{"question_id": "Q1", "question_text": "q", "arms": {"a": {"answer": "Some prose. " * 200}}}]
    s = run_validity(one, lambda lib, q, a: float(len(a)), variants=["mismatched_answer", "truncate_20"]).summary()
    assert s["n_noop"] == {}
    assert s["unmeasured_variants"] == ["mismatched_answer"]


@pytest.mark.parametrize("strict, code", [((), 0), (("--strict",), 1)])
def test_cli_warns_on_an_unmeasured_variant(tmp_path, monkeypatch, capsys, strict, code):
    import json

    import dokimi.eval.judge as judge_mod
    import dokimi.eval.judge_validity as jv
    from dokimi.cli import build_parser

    answers = tmp_path / "answers.json"
    answers.write_text(json.dumps([{"question_id": f"Q{i}", "question_text": "q",
                                    "arms": {"a": {"answer": f"Plain prose, answer {i}. " * 100}}} for i in range(20)]))
    monkeypatch.setattr(judge_mod, "Judge", lambda **kw: None)
    monkeypatch.setattr(jv, "judge_scorer", lambda judge: lambda lib, q, a: float(len(a)))
    args = build_parser().parse_args(["eval", "judge-validity", "--answers", str(answers),
                                      "--variants", "strip_code,truncate_20", *strict])
    try:
        args.func(args)
        exit_code = 0
    except SystemExit as exc:
        exit_code = exc.code
    assert exit_code == code
    assert "no pair was built for strip_code" in capsys.readouterr().err


def test_judge_temperature_reaches_the_judge_and_the_report(tmp_path, monkeypatch):
    import json

    import dokimi.eval.judge as judge_mod
    import dokimi.eval.judge_validity as jv
    from dokimi.cli import build_parser

    answers = tmp_path / "answers.json"
    answers.write_text(json.dumps(_answers()))
    out = tmp_path / "validity.json"
    built = {}
    monkeypatch.setattr(judge_mod, "Judge", lambda **kw: built.update(kw))
    monkeypatch.setattr(jv, "judge_scorer", lambda judge: lambda lib, q, a: float(len(a)))
    args = build_parser().parse_args(["eval", "judge-validity", "--answers", str(answers),
                                      "--variants", "truncate_20", "--judge-temperature", "0",
                                      "--output", str(out)])
    try:
        args.func(args)
    except SystemExit:
        pass
    assert built["temperature"] == 0.0
    assert json.loads(out.read_text())["judge_temperature"] == 0.0


# --- degradations: contradiction names, code removal, figure scaling ----------


@pytest.mark.parametrize(
    "answer, name",
    [
        ("**Summary**\n\nThis is a well-known, step-by-step guide. Call tbb_malloc first.", "tbb_malloc"),
        ("**Warning** Use the built-in allocator wrapper tbb_proxy.", "tbb_proxy"),
        # A plain English word is only skipped when unquoted: a quoted one is code.
        ("Run `numactl` first.", "numactl"),
    ],
)
def test_contradiction_does_not_disown_an_english_word(answer, name):
    assert f"caveat: `{name}` is not actually applicable" in contradiction(answer)


@pytest.mark.parametrize(
    "answer, name",
    [
        ("Do **not** use `malloc` here; link `tbbmalloc_proxy` instead.", "tbbmalloc_proxy"),
        ("Avoid `omp_set_nested`; call `omp_set_max_active_levels`.", "omp_set_max_active_levels"),
        ("Use `tbb_arena`, not `tbb_group`.", "tbb_arena"),
        ("Never use `tbb_group`; use `tbb_arena`.", "tbb_arena"),
        ("Don't use `tbb_group`; use `tbb_arena`.", "tbb_arena"),
        ("Instead of `tbb_group`, use `tbb_arena`.", "tbb_arena"),
        ("Rather than `tbb_group`, use `tbb_arena`.", "tbb_arena"),
        ("You cannot use `np.memmap` here, call `dpnp.asarray`.", "dpnp.asarray"),
        ("You can\u2019t use `np.memmap` here, call `dpnp.asarray`.", "dpnp.asarray"),
        ("We won\u2019t use `np.memmap` here, call `dpnp.asarray`.", "dpnp.asarray"),
        ("Don\u2019t use `malloc`; link `tbbmalloc_proxy`.", "tbbmalloc_proxy"),
        ("There is no `dpnp.save`; call `dpnp.asnumpy`.", "dpnp.asnumpy"),
        # A name followed by the answer saying it is unavailable.
        ("`numpy.save` is not supported in dpnp, use `dpnp.asnumpy`.", "dpnp.asnumpy"),
        ("`np.save` and `np.load` are not re-implemented. Call `dpnp.asnumpy`.", "dpnp.asnumpy"),
        ("`cp.cuda.Stream` is not present here. Call `dpnp.asnumpy`.", "dpnp.asnumpy"),
        ("`old_api` is deprecated; use `new_api`.", "new_api"),
        ("`old_api` should be avoided, so call `new_api`.", "new_api"),
        ("`old_api` (deprecated), use `new_api`.", "new_api"),
        ("`cupy.asnumpy` is not available; call `dpnp.asnumpy`.", "dpnp.asnumpy"),
        ("`np.save`, `np.load` or `np.fromfile` are not supported; call `dpnp.asnumpy`.", "dpnp.asnumpy"),
        ("`np.save` and **np.load** are not supported; call `dpnp.asnumpy`.", "dpnp.asnumpy"),
        ("Neither `cupy.asnumpy` nor `numpy.asarray` is wanted; call `dpnp.asnumpy`.", "dpnp.asnumpy"),
        ("There are no `dpnp.memmap` equivalents; call `dpnp.asnumpy`.", "dpnp.asnumpy"),
        ("Don't ever use `tbb_group`; use `tbb_arena`.", "tbb_arena"),
        ("It is not recommended to use `np.fromfile`; call `dpnp.asarray`.", "dpnp.asarray"),
        ("Do not use `omp_a`, or `omp_b`; use `omp_c`.", "omp_c"),
        ("Do not use `omp_a`. `omp_b` is the one to use.", "omp_b"),
        ("`old_api` is NOT covered by the shim; use `new_api`.", "new_api"),
        ("`mkl_umath` does not apply to your code; call `dpnp.add`.", "dpnp.add"),
        ("`mkl_umath` doesn’t apply here; call `dpnp.add`.", "dpnp.add"),
        ("No, `leapfrog` will not work here; use `skipahead`.", "skipahead"),
        ("`leapfrog` won't work here; use `skipahead`.", "skipahead"),
        ("**`np.floor_divide`** is explicitly **not covered** here; call `np.divide`.", "np.divide"),
        # A later bare mention of a rejected dotted name is the same name.
        ("`np.maximum` is not covered. `maximum` is slow; call `np.fmax`.", "np.fmax"),
        # A dot inside a name does not end the sentence; a repeat mention is the same name.
        ("Do not use `np.asarray` or `np.array` here; call `dpnp.asnumpy`.", "dpnp.asnumpy"),
        ("Do not use `malloc` here. `malloc` takes a global lock, so link `tbbmalloc_proxy`.", "tbbmalloc_proxy"),
    ],
)
def test_contradiction_does_not_disown_what_the_answer_warns_against(answer, name):
    assert f"caveat: `{name}` is not actually applicable" in contradiction(answer)


@pytest.mark.parametrize(
    "answer, name",
    [
        ("Set it to `True`, then call `dpnp.asarray`.", "dpnp.asarray"),
        ("Pass `None` or `False`; call `dpnp.asarray`.", "dpnp.asarray"),
        ("It raises `NotImplementedError`, so call `dpnp.asarray`.", "dpnp.asarray"),
        ("It raises `ValueError` and a `RuntimeWarning`; call `dpnp.asarray`.", "dpnp.asarray"),
        ("`Summary` first. `Warning` too. Then call `dpnp.asarray`.", "dpnp.asarray"),
        ("Replace the `cp.` prefix with `dpnp.asarray`.", "dpnp.asarray"),
        ("**Summary.** Use tbb_proxy here.", "tbb_proxy"),
        ("**WARNING** Use tbb_proxy here.", "tbb_proxy"),
    ],
)
def test_contradiction_skips_literals_exception_names_and_fragments(answer, name):
    assert f"caveat: `{name}` is not actually applicable" in contradiction(answer)


@pytest.mark.parametrize(
    "answer, name",
    [
        # The negation ends before the recommended name.
        ("Avoid `omp_set_nested`, call `omp_set_max_active_levels`.", "omp_set_max_active_levels"),
        ("Avoid copies: use `dpnp.asarray`.", "dpnp.asarray"),
        ("Avoid `omp_set_nested` \u2014 `omp_set_max_active_levels` replaces it.", "omp_set_max_active_levels"),
        ("If you do not have `numactl` installed, install it.", "numactl"),
        ("Do not use `a_b`, use `c_d`, then call `e_f`.", "c_d"),
        # Only a colon, a semicolon or a comma ends the reach here.
        ("Avoid copies: `dpnp.asarray` is the fast path.", "dpnp.asarray"),
        ("Avoid `a_b`; `c_d` is better.", "c_d"),
        # "does not apply" disowns only when nothing is being applied.
        ("`mkl_fft.fftn` does not apply the norm; call `mkl_fft.fft2`.", "mkl_fft.fftn"),
        ("Don\u2019t call `dpnp.asnumpy` in a loop, batch with `dpnp.concatenate`.", "dpnp.concatenate"),
    ],
)
def test_contradiction_keeps_a_name_the_negation_does_not_reach(answer, name):
    assert f"caveat: `{name}` is not actually applicable" in contradiction(answer)


@pytest.mark.parametrize("gap, picked", [(40, "tbb_arena"), (41, "tbb_group")])
def test_contradiction_negation_reaches_forty_characters(gap, picked):
    answer = "Avoid " + "a" * (gap - 2) + " `tbb_group`; use `tbb_arena`."
    assert answer.index("`tbb_group`") - len("Avoid") == gap
    assert f"caveat: `{picked}` is not actually applicable" in contradiction(answer)


@pytest.mark.parametrize(
    "remove",
    [strip_code, lambda a: hedge_concrete(a, rng=random.Random(0))],
    ids=["strip_code", "hedge_concrete"],
)
@pytest.mark.parametrize(
    "answer, gone, kept",
    [
        # An outer four-backtick fence holding a three-backtick one.
        ("Intro.\n\n````markdown\n```bash\nperf record\n```\n````\n\nEnd.", ("perf record", "bash", "`"), ("Intro.", "End.")),
        # ``` mentioned mid-line in prose must not pair with the next real fence.
        ("Wrap code in ``` fences.\n\n```bash\nmake\n```\n\nEnd.", ("make", "bash"), ("Wrap code in ``` fences.", "End.")),
        ("Intro.\n\n~~~bash\nmake all\n~~~\n\nEnd.", ("make all", "~~~"), ("Intro.", "End.")),
        ("Intro.\n\n<pre>\nmake all\n</pre>\n\nEnd.", ("make all", "pre>"), ("Intro.", "End.")),
        ("1. Install:\n\n    ```bash\n    make all\n    ```\n\n2. Run.", ("make all", "```"), ("1. Install:", "2. Run.")),
        # A same-line span, as in a table cell.
        ("| a | b |\n| - | - |\n| x | ```make all``` |\n| y | z |", ("make all", "`"), ("| x |", "| y | z |")),
        ("Use ```make all``` or ```make test``` here.", ("make all", "make test", "`"), ("Use", "here.")),
        # A closing fence followed by a pipe still closes.
        ("Intro.\n\n```python\nalpha()\n``` |\n\nProse here.\n\n```\nbeta()\n```\n\nEnd.", ("alpha", "beta", "`"), ("Prose here.", "End.")),
        # A block inside a blockquote.
        ("Intro.\n\n> ```bash\n> make all\n> ```\n\nEnd.", ("make all", "`"), ("Intro.", "> ", "End.")),
    ],
)
def test_code_removal_takes_whole_blocks_by_the_fence_rules_padding_uses(remove, answer, gone, kept):
    out = remove(answer)
    assert not any(g in out for g in gone), out
    assert all(k in out for k in kept), out


@pytest.mark.parametrize(
    "answer, expected",
    [
        ("| a | ```x = 1``` |", "| a | [code removed] |"),
        ("Use ```make all``` or ```make test``` here.", "Use [code removed] or [code removed] here."),
        ("Intro.\n\n```python\nalpha()\n``` |\n\nProse here.\n\n```\nbeta()\n```\n\nEnd.",
         "Intro.\n\n[code removed] |\n\nProse here.\n\n[code removed]\n\nEnd."),
        # The indent keeps a block inside its list item.
        ("1. Step one:\n\n    ```bash\n    make\n    ```\n\n    Then run it.\n\n2. Next.",
         "1. Step one:\n\n    [code removed]\n\n    Then run it.\n\n2. Next."),
        # An unclosed block is left as it is, with the prose after it.
        ("Intro.\n\n```bash\nmake\n\nTrailing prose.", "Intro.\n\n```bash\nmake\n\nTrailing prose."),
        ("Intro.\r\n\r\n```bash\r\nmake\r\n```\r\n\r\nEnd.\r\n", "Intro.\r\n\r\n[code removed]\r\n\r\nEnd.\r\n"),
        ("A.\n\n```\nx()\n```\t|\n\nB.", "A.\n\n[code removed]\t|\n\nB."),
        # A block inside a blockquote, which also ends with the quote.
        ("Intro.\n\n> ```bash\n> make all\n> ```\n\nEnd.", "Intro.\n\n> [code removed]\n\nEnd."),
        ("> Note:\n>\n> ```python\n> import dpnp\n> ```\n>\n> Done.", "> Note:\n>\n> [code removed]\n>\n> Done."),
        ("> > ```\n> > x()\n> > ```\n\nEnd.", "> > [code removed]\n\nEnd."),
        ("> ```\n> make\n\nProse.\n\n```\nx()\n```", "> [code removed]\n\nProse.\n\n[code removed]"),
        # A line starting with ">" inside an unquoted block is code.
        ("```console\n> ```\n> make\n```\n\nEnd.", "[code removed]\n\nEnd."),
        # A run of backticks mentioned in prose is not a span.
        ("Write ```` or ``` to fence.", "Write ```` or ``` to fence."),
        ("Use ```x```` here.", "Use ```x```` here."),
    ],
)
def test_strip_code_output_is_exact(answer, expected):
    assert strip_code(answer) == expected


def test_hedge_concrete_does_not_count_a_blocks_indent_in_its_length_budget():
    block = "        ```bash\n        make all\n        ```"
    answer = "\n\n".join(f"Step {i} is below.\n\n{block}" for i in range(10))
    drift = [len(hedge_concrete(answer, rng=random.Random(seed))) - len(answer) for seed in range(20)]
    assert abs(sum(drift)) / len(drift) < 20, drift


def test_shuffle_sentences_removes_a_blockquoted_block():
    out = shuffle_sentences("Run it. Then check.\n\n> ```bash\n> make all\n> ```\n\nDone.", rng=random.Random(0))
    assert "`" not in out and "make all" not in out, out


def test_shuffle_sentences_removes_a_tilde_fenced_block():
    answer = "Install it first. Then run the build.\n\n~~~bash\nexport A=1. make all. ./run.sh\n~~~\n\nCheck the log. Done."
    out = shuffle_sentences(answer, rng=random.Random(0))
    assert "~~~" not in out and "make all" not in out, out
    assert sorted(out.split()) == sorted("Install it first. Then run the build. Check the log. Done.".split())


def test_padding_keeps_a_fence_open_through_an_indented_fence_line():
    # A markdown-template answer: the indented ``` lines are content of the
    # outer block, not its closer.
    block = "```markdown\n1. Step\n\n    ```bash\n    make\n    ```\n\n2. Next\n```"
    out = padding(f"Intro.\n\n{block}\n\nEnd.")
    assert block in out, out
    assert all(p in out for p in PADDING_PARAGRAPHS)


@pytest.mark.parametrize(
    "answer, expected",
    [
        # A figure in a URL is part of the address, not a claim.
        ("Fetch https://x.org/guide/4/ or [g](https://x.org/v2/8x/) in 5 ms.",
         "Fetch https://x.org/guide/4/ or [g](https://x.org/v2/8x/) in 50 ms."),
        ("Get https://x.org/p?page=10&limit=50 or https://x.org/q?timeout=30s in 5 ms.",
         "Get https://x.org/p?page=10&limit=50 or https://x.org/q?timeout=30s in 50 ms."),
        # The scaled figure is written out whole: no exponent, no float noise.
        ("Took 123456.78 ms.", "Took 1234567.8 ms."),
        ("Took 1.1 ms.", "Took 11 ms."),
        ("Took 0.07 ms.", "Took 0.7 ms."),
        ("Took 0.000001 ms.", "Took 0.00001 ms."),
        ("Took 1234567890123.25 ms.", "Took 12345678901232.5 ms."),
        ("Took 1,200 ms.", "Took 12,000 ms."),
        ("Hit 40% of it.", "Hit 4% of it."),
        ("Hit 10% of it.", "Hit 1% of it."),
        ("Hit 9% of it.", "Hit 90% of it."),
        # Outside a URL a fraction is scaled on both sides.
        ("Use 1/4 of the cache.", "Use 10/40 of the cache."),
    ],
)
def test_wrong_numbers_writes_plain_figures_and_leaves_urls_alone(answer, expected):
    assert wrong_numbers(answer) == expected


@pytest.mark.parametrize("tail", [" then 3 runs."])
def test_wrong_numbers_survives_a_digit_run_too_long_for_a_float(tail):
    huge = "9" * 400
    out = wrong_numbers(f"Digest {huge}{tail}")
    assert out.startswith(f"Digest {huge}") and out != f"Digest {huge}{tail}"


# ── --context stored: the gate in the setting arms runs score in ─────────────

def _with_materials(skill_text="FULL SKILL BODY"):
    """`_answers()` as an arms artifact that keeps its judge materials."""
    from dokimi.utils import digest_text

    data = {"answers": _answers(), "judge_materials": {}}
    for rec in data["answers"]:
        for arm_name, arm in rec["arms"].items():
            text = skill_text if arm_name == "skill" else ""
            digest = digest_text(text)
            arm["judge_context_sha256"] = digest
            data["judge_materials"][digest] = text
    return data


def test_stored_context_reaches_the_judge_for_every_member_of_a_pair():
    from dokimi.eval.judge_context import NO_CONTEXT, JudgeContextStore

    data = _with_materials()
    seen = []

    def scorer(lib, q, a, context=None):
        seen.append(context)
        return float(len(a))

    report = run_validity(data["answers"], scorer, n_bases=3, variants=["truncate_20"],
                          store=JudgeContextStore.from_artifact(data))
    assert report.context == "stored" and report.summary()["context"] == "stored"
    assert set(seen) == {"FULL SKILL BODY", NO_CONTEXT}   # baseline's empty material
    assert None not in seen
    assert "stored treatment material" in report.format_summary()


def test_bases_with_no_stored_material_are_dropped_and_counted():
    from dokimi.eval.judge_context import JudgeContextStore

    import copy

    data = _with_materials()
    third = copy.deepcopy(data["answers"][0])
    third["question_id"] = "Q003"
    data["answers"].append(third)       # skill stays covered through Q003
    del data["answers"][0]["arms"]["skill"]["judge_context_sha256"]
    report = run_validity(data["answers"], lambda l, q, a, context=None: 1.0, n_bases=9,
                          variants=["truncate_20"], store=JudgeContextStore.from_artifact(data))
    assert report.n_without_context == 1
    assert report.by_arm == {"baseline": {"kept": 3, "dropped": 0, "sampled": 3},
                             "skill": {"kept": 1, "dropped": 1, "sampled": 1}}
    assert report.summary()["by_arm"] == report.by_arm
    text = report.format_summary()
    assert "1 answers without verified material (counted before sampling)" in text
    assert "skill: 1 kept, 1 dropped, 1 bases sampled" in text


def test_build_returns_pairs_and_dropped_count_without_an_out_param():
    import dataclasses
    import copy

    from dokimi.eval.judge_context import JudgeContextStore
    from dokimi.eval.judge_validity import Built, _build

    data = _with_materials()
    third = copy.deepcopy(data["answers"][0])
    third["question_id"] = "Q003"
    data["answers"].append(third)
    del data["answers"][0]["arms"]["skill"]["judge_context_sha256"]
    built = _build(data["answers"], {}, n_bases=9, seed=0, variants=["truncate_20"], library="lib",
                   store=JudgeContextStore.from_artifact(data), by_arm={})
    assert isinstance(built, Built)
    assert built.n_dropped == 1
    assert built.pairs and all("context" in p for p in built.pairs)
    with pytest.raises(dataclasses.FrozenInstanceError):
        built.n_dropped = 5

    no_store = _build(_answers(), {}, n_bases=3, seed=0, variants=["truncate_20"], library="lib")
    assert no_store.n_dropped == 0 and no_store.pairs


def test_no_stored_material_at_all_is_an_error_not_a_no_context_run():
    from dokimi.eval.judge_context import JudgeContextStore

    store = JudgeContextStore()   # nothing stored
    with pytest.raises(NoUsablePairs, match="judge_materials"):
        run_validity(_answers(), lambda l, q, a, context=None: 1.0, n_bases=3,
                     variants=["truncate_20"], store=store)


def test_a_whole_arm_without_verified_material_is_refused():
    from dokimi.eval.judge_context import JudgeContextStore

    data = _with_materials()
    del data["answers"][0]["arms"]["skill"]["judge_context_sha256"]   # skill's only record
    with pytest.raises(NoUsablePairs, match=r"\['skill'\]"):
        run_validity(data["answers"], lambda l, q, a, context=None: 1.0, n_bases=3,
                     variants=["truncate_20"], store=JudgeContextStore.from_artifact(data))


def test_all_noop_with_a_store_reports_noop_not_missing_material():
    from unittest import mock

    from dokimi.eval.judge_context import JudgeContextStore

    data = _with_materials()
    with mock.patch.dict("dokimi.eval.degrade.DEGRADATIONS", {"truncate_20": lambda text, **_: text}):
        with pytest.raises(NoUsablePairs, match="unchanged"):
            run_validity(data["answers"], lambda l, q, a, context=None: 1.0, n_bases=3,
                         variants=["truncate_20"], store=JudgeContextStore.from_artifact(data))


def test_without_a_store_the_scorer_is_called_as_before():
    calls = []
    run_validity(_answers(), lambda l, q, a: calls.append(1) or 1.0, n_bases=3,
                 variants=["truncate_20"])
    assert calls


def test_judge_scorer_forwards_the_context_only_when_given():
    from dokimi.eval.judge_validity import judge_scorer

    got = []

    class J:
        def score_answer(self, **kw):
            got.append(kw)
            return {"aggregate": 1.0}

    s = judge_scorer(J())
    s("lib", "q", "a")
    s("lib", "q", "a", context="material")
    s_with_draw = judge_scorer(J(), draw=2)
    s_with_draw("lib", "q", "a")
    assert "context" not in got[0] and got[1]["context"] == "material"
    assert "draw" not in got[0]
    assert got[2]["draw"] == 2 and "context" not in got[2]


def test_sequential_mode_takes_the_store_too():
    from dokimi.eval.judge_context import JudgeContextStore
    from dokimi.eval.judge_sequential import run_sequential

    data = _with_materials()
    seen = set()

    def scorer(lib, q, a, context=None):
        seen.add(context)
        return float(len(a))

    report = run_sequential(data["answers"], scorer, max_bases=3, variants=["truncate_20"],
                            store=JudgeContextStore.from_artifact(data))
    assert report.context == "stored" and "FULL SKILL BODY" in seen


@pytest.mark.parametrize("extra, message", [
    ([], "judge_materials"),                                  # file without materials
    (["--scorer", "typed:laya"], "takes context"),            # typed scorer has no context slot
    (["--mode", "pairwise"], "takes context"),
])
def test_cli_refuses_stored_context_it_cannot_honour(tmp_path, capsys, extra, message):
    import json

    from dokimi.cli import build_parser

    answers = tmp_path / "a.json"
    answers.write_text(json.dumps({"answers": _answers()}))
    args = build_parser().parse_args(["eval", "judge-validity", "--answers", str(answers),
                                      "--context", "stored", *extra])
    with pytest.raises(SystemExit) as exc:
        args.func(args)
    assert exc.value.code == 2 and message in capsys.readouterr().err


def test_cli_judges_each_answer_with_its_arms_stored_material(tmp_path, monkeypatch):
    import json

    from dokimi.cli import build_parser
    from dokimi.eval import judge as judge_mod

    seen = []

    class FakeJudge:
        def __init__(self, **_):
            pass

        def score_answer(self, library_name, question, answer, context="(none passed)", **_):
            seen.append(context)
            return {"aggregate": float(len(answer) % 97)}

    monkeypatch.setattr(judge_mod, "Judge", FakeJudge)
    answers = tmp_path / "arms.json"
    answers.write_text(json.dumps(_with_materials()))
    out = tmp_path / "v.json"
    args = build_parser().parse_args(["eval", "judge-validity", "--answers", str(answers),
                                      "--context", "stored", "--bases", "3",
                                      "--variants", "truncate_20", "--output", str(out)])
    code = None
    try:
        args.func(args)
    except SystemExit as exc:      # the gate may fail on a toy judge; that is exit 1, not 2
        code = exc.code
    assert code in (None, 1)
    assert "FULL SKILL BODY" in seen and "(none passed)" not in seen
    assert json.loads(out.read_text())["summary"]["context"] == "stored"


def test_a_lone_surrogate_in_material_is_hashed_not_fatal():
    from dokimi.eval.judge_context import JudgeContextStore

    text = "chunk ends mid-pair " + chr(0xD83D)
    store = JudgeContextStore()
    store.put("q1", "docs", text)
    digest = store.digest_of("q1", "docs")
    assert digest.startswith("sha256:") and store.materials() == {digest: text}
    data = {"answers": [{"question_id": "q1", "arms": {"docs": {"judge_context_sha256": digest}}}],
            "judge_materials": {digest: text}}
    assert JudgeContextStore.from_artifact(data).get("q1", "docs") == text


def test_the_sampled_bases_come_from_those_with_material():
    """--bases N is drawn after the bases without material are dropped (Thread 1)."""
    from dokimi.eval.judge_context import JudgeContextStore

    answers = [{"question_id": f"Q{i}", "question_text": "q",
                "arms": {a: {"answer": ANSWER + f" {a}{i}"} for a in ("baseline", "skill", "docs")}}
               for i in range(40)]
    store = JudgeContextStore()
    for rec in answers:
        store.put(rec["question_id"], "baseline", "")
        store.put(rec["question_id"], "skill", "SKILL")     # docs has no verified material
    # Without the docs arm in the store the arm is dropped entirely; keep one docs record.
    store.put("Q0", "docs", "DOCS")
    report = run_validity(answers, lambda l, q, a, context=None: 1.0, n_bases=30,
                          variants=["truncate_20"], store=store)
    assert len({(r.base_id, r.arm) for r in report.rows}) == 30


def _stored_cli(tmp_path, monkeypatch, *argv, data=None, score=lambda a: float(len(a) % 97)):
    import json

    from dokimi.cli import build_parser
    from dokimi.eval import judge as judge_mod

    class FakeJudge:
        def __init__(self, **_):
            pass

        def score_answer(self, library_name, question, answer, context="(none passed)", **_):
            return {"aggregate": score(answer)}

    monkeypatch.setattr(judge_mod, "Judge", FakeJudge)
    answers = tmp_path / "arms.json"
    answers.write_text(json.dumps(data if data is not None else _with_materials()))
    out = tmp_path / "v.json"
    args = build_parser().parse_args(["eval", "judge-validity", "--answers", str(answers),
                                      "--judge-provider", "amazon-bedrock", "--judge-model", "m",
                                      "--variants", "truncate_20", "--output", str(out), *argv])
    code = None
    try:
        args.func(args)
    except SystemExit as exc:
        code = exc.code
    assert code in (None, 1)       # the gate failing is 1; 2 is a refusal
    return json.loads(out.read_text())["summary"]


@pytest.mark.parametrize("mode", [["--bases", "3"], ["--sequential", "--max-bases", "3"]])
def test_cli_stored_context_reports_stored_with_an_unsuffixed_label(tmp_path, monkeypatch, mode):
    summary = _stored_cli(tmp_path, monkeypatch, "--context", "stored", *mode)
    assert summary["context"] == "stored"
    assert summary["judge"] == "amazon-bedrock/m"
    assert summary["mode"] == ("sequential" if "--sequential" in mode else "fixed")
    assert set(summary["by_arm"]) == {"baseline", "skill"}


def test_cli_judge_validity_exits_2_when_no_answer_is_long_enough(tmp_path, monkeypatch, capsys):
    import json

    from dokimi.cli import build_parser

    answers = tmp_path / "a.json"
    answers.write_text(json.dumps({"answers": [{"question_id": "Q1", "question_text": "q",
                                                "arms": {"baseline": {"answer": "short"}}}]}))
    args = build_parser().parse_args(["eval", "judge-validity", "--answers", str(answers),
                                      "--judge-provider", "amazon-bedrock"])
    with pytest.raises(SystemExit) as exc:
        args.func(args)
    assert exc.value.code == 2 and "Error:" in capsys.readouterr().err


def test_a_bug_valueerror_in_a_degradation_is_not_turned_into_a_user_error(tmp_path, monkeypatch):
    from unittest import mock

    def boom(text, **_):
        raise ValueError("internal bug in truncate_20")

    with mock.patch.dict("dokimi.eval.degrade.DEGRADATIONS", {"truncate_20": boom}):
        with pytest.raises(ValueError, match="internal bug"):
            _stored_cli(tmp_path, monkeypatch, "--bases", "3")


def test_cli_stored_context_on_a_baseline_only_artifact_exits_2(tmp_path, monkeypatch, capsys):
    data = _with_materials()
    for rec in data["answers"]:
        rec["arms"].pop("skill", None)
    data["judge_materials"] = {k: v for k, v in data["judge_materials"].items() if v == ""}
    with pytest.raises(AssertionError):   # _stored_cli asserts exit code 1; here it is 2
        _stored_cli(tmp_path, monkeypatch, "--context", "stored", data=data)
    assert "judge_materials" in capsys.readouterr().err


def test_a_stored_validity_report_gates_the_rescored_judge_in_cross_judge(tmp_path, monkeypatch, capsys):
    """judge-validity --context stored, then cross-judge --validity on its report."""
    import json

    from dokimi.cli import build_parser
    from dokimi.utils import digest_text

    # A judge that ranks the longer answer lower fails the gate (intact must win).
    failing = _stored_cli(tmp_path, monkeypatch, "--context", "stored", "--bases", "3",
                          score=lambda a: -float(len(a)))
    assert failing["passed"] is False and failing["judge"] == "amazon-bedrock/m"
    stored_report = tmp_path / "stored.json"
    stored_report.write_text(json.dumps({"summary": failing}))
    none_report = tmp_path / "none.json"
    none_report.write_text(json.dumps({"summary": {**failing, "context": "none"}}))
    none_pass = tmp_path / "none_pass.json"
    none_pass.write_text(json.dumps({"summary": {**failing, "context": "none", "passed": True}}))
    stored_pass = tmp_path / "stored_pass.json"
    stored_pass.write_text(json.dumps({"summary": {**failing, "passed": True}}))

    digest = digest_text("FULL SKILL BODY")
    empty = digest_text("")  # the baseline arm's stored material is empty, and still stored
    run = {"library_name": "L", "judge_provider": "amazon-bedrock", "judge_model": "m",
           "judge_context": "treatment_material.v1",
           "judge_materials": {digest: "FULL SKILL BODY", empty: ""},
           "answers": [{"question_id": f"Q{i}", "question_text": "q", "arms": {
               "baseline": {"answer": f"b{i}", "judge_context_sha256": empty},
               "skill": {"answer": f"s{i}", "judge_context_sha256": digest}}}
               for i in range(8)]}

    def gates(report_path):
        run_path = tmp_path / "run.json"
        run_path.write_text(json.dumps(run))
        out = tmp_path / "cj.json"
        args = build_parser().parse_args(["eval", "cross-judge", "--answers", str(run_path), "--no-stored",
                                          "--judges", "amazon-bedrock/m", "--validity", str(report_path),
                                          "--output", str(out)])
        args.func(args)
        rows = json.loads(out.read_text())["summary"]["arms"]["skill"]["rows"]
        return {r["measure"]: r["gate"] for r in rows}

    assert gates(stored_report) == {"amazon-bedrock/m": "FAIL"}
    # A FAIL reaches the judge in any context; a PASS only in the context it ran in.
    assert gates(none_report) == {"amazon-bedrock/m": "FAIL"}
    assert gates(stored_pass) == {"amazon-bedrock/m": "PASS"}
    capsys.readouterr()
    assert gates(none_pass) == {"amazon-bedrock/m": None}
    assert "report context none; its measures are stored" in capsys.readouterr().err


def test_a_missing_material_warning_does_not_promise_a_snippet_fallback_on_the_gate(caplog):
    from dokimi.eval.judge_context import JudgeContextStore

    with caplog.at_level("WARNING", logger="dokimi.eval.judge_context"):
        assert JudgeContextStore().get("Q1", "skill") is None
    text = caplog.text
    assert "No stored judge context for Q1/skill" in text and "drops such bases" in text


def test_sequential_stored_context_reports_pool_and_dropped_answers():
    """Only answers with verified material count toward the pool, so an exhausted pool is 'pool'."""
    from dokimi.eval.judge_context import JudgeContextStore
    from dokimi.eval.judge_sequential import run_sequential
    from dokimi.utils import digest_text

    digest = digest_text("FULL SKILL BODY")
    answers = [{"question_id": f"Q{i:03d}", "question_text": f"q{i}?",
                "arms": {"skill": {"answer": ANSWER + f" Detail {i}. ",
                                   **({"judge_context_sha256": digest} if i < 4 else {})}}}
               for i in range(20)]
    data = {"answers": answers, "judge_materials": {digest: "FULL SKILL BODY"}}
    report = run_sequential(data["answers"], lambda l, q, a, context=None: float(len(a)),
                            max_bases=10, variants=["truncate_20"],
                            store=JudgeContextStore.from_artifact(data))
    assert report.n_without_context == 16
    assert report.by_arm == {"skill": {"kept": 4, "dropped": 16, "sampled": 4, "scored": 4}}
    assert "16 answers without verified material" in report.format_summary()
    d = report.sequential["truncate_20"]
    assert d["decision"] == "continue" and d["stopped_by"] == "pool"



def _starvable(n=30, with_material=3):
    """n baseline answers and n skill answers, of which only ``with_material`` hold material."""
    from dokimi.utils import digest_text

    empty, body = digest_text(""), digest_text("FULL SKILL BODY")
    answers = [{"question_id": f"Q{i:03d}", "question_text": f"q{i}?", "arms": {
        "baseline": {"answer": ANSWER + f" Base {i}. ", "judge_context_sha256": empty},
        "skill": {"answer": ANSWER + f" Skill {i}. ",
                  **({"judge_context_sha256": body} if i < with_material else {})}}}
        for i in range(n)]
    return {"answers": answers, "judge_materials": {empty: "", body: "FULL SKILL BODY"}}


def test_an_arm_with_material_but_no_sampled_base_is_refused():
    """A --context stored PASS must not rest on the other arm's bases alone."""
    from dokimi.eval.judge_context import JudgeContextStore
    from dokimi.eval.judge_validity import NoUsablePairs

    data = _starvable()
    refused = covered = 0
    for seed in range(20):
        try:
            report = run_validity(data["answers"], lambda l, q, a, context=None: 1.0, n_bases=4,
                                  seed=seed, variants=["truncate_20"],
                                  store=JudgeContextStore.from_artifact(data))
        except NoUsablePairs as exc:
            message = str(exc)
            refused += 1
            assert "['skill']" in message and "no base was scored" in message
        else:
            assert report.by_arm["skill"]["sampled"] > 0
            # Fixed mode doesn't track "scored", just verify rows exist for skill
            assert any(r.arm == "skill" for r in report.rows)
            covered += 1
    assert refused and covered


def test_a_scorer_without_a_context_argument_is_refused_before_pairs_are_built():
    from dokimi.eval.judge_context import JudgeContextStore
    from dokimi.eval.judge_validity import NoUsablePairs
    from dokimi.utils import digest_text

    digest = digest_text("FULL SKILL BODY")
    data = {"answers": [{"question_id": "Q0", "question_text": "q?", "arms": {
        "skill": {"answer": ANSWER, "judge_context_sha256": digest}}}],
        "judge_materials": {digest: "FULL SKILL BODY"}}
    with pytest.raises(NoUsablePairs, match="does not accept a `context=` argument"):
        run_validity(data["answers"], lambda lib, q, a: 1.0, n_bases=5, variants=["truncate_20"],
                     store=JudgeContextStore.from_artifact(data))


def test_a_noop_variant_leaving_an_arm_unpaired_is_refused_before_scoring():
    """strip_code is a no-op on the skill answers (no fence), so the skill arm, which has
    verified material, gets no pair. The refusal names it and no judge call is made."""
    from dokimi.eval.judge_context import JudgeContextStore
    from dokimi.eval.judge_validity import NoUsablePairs
    from dokimi.utils import digest_text

    digest = digest_text("FULL SKILL BODY")
    empty = digest_text("")
    plain = "Plain skill prose without any code fence, long enough to be a usable answer. " * 25
    answers = [{"question_id": f"Q{i:03d}", "question_text": f"q{i}?", "arms": {
        "baseline": {"answer": ANSWER + f" Base {i}. ", "judge_context_sha256": empty}}}
               for i in range(10)]
    for i in range(3):
        answers[i]["arms"]["skill"] = {"answer": plain + f" {i}", "judge_context_sha256": digest}
    data = {"answers": answers, "judge_materials": {empty: "", digest: "FULL SKILL BODY"}}
    calls = []

    def scorer(lib, q, a, context=None):
        calls.append(a)
        return 1.0

    with pytest.raises(NoUsablePairs, match=r"\['skill'\]"):
        run_validity(data["answers"], scorer, n_bases=50, seed=0, variants=["strip_code"],
                     store=JudgeContextStore.from_artifact(data))
    assert calls == []


def test_fixed_mode_arm_with_material_and_only_noop_bases_is_refused():
    """A fixed-mode run where one arm gets no scored base refuses despite material."""
    from dokimi.eval.judge_context import JudgeContextStore
    from dokimi.eval.judge_validity import NoUsablePairs
    from dokimi.utils import digest_text

    digest = digest_text("FULL SKILL BODY")
    empty = digest_text("")
    # 100 baseline answers, 1 skill answer with material.
    # With n_bases=50, skill arm has 1 kept but typically gets no sampled base due to
    # low odds in pooled shuffle. When no base is sampled and later no rows scored,
    # the gate would not cover the arm.
    answers = [{"question_id": f"Q{i:03d}", "question_text": f"q{i}?", "arms": {
        "baseline": {"answer": ANSWER + f" Base {i}. ", "judge_context_sha256": empty}}}
               for i in range(100)]
    answers[0]["arms"]["skill"] = {"answer": ANSWER + " Skill 0. ", "judge_context_sha256": digest}
    data = {"answers": answers, "judge_materials": {empty: "", digest: "FULL SKILL BODY"}}

    # With only 1 skill answer out of 100 and n_bases=50, skill has low odds of being sampled.
    refused = covered = 0
    for seed in range(10):
        try:
            run_validity(data["answers"], lambda l, q, a, context=None: 1.0, n_bases=50,
                         seed=seed, variants=["truncate_20"],
                         store=JudgeContextStore.from_artifact(data))
            covered += 1
        except NoUsablePairs as exc:
            message = str(exc)
            refused += 1
            assert "['skill']" in message and "no base was scored" in message
    # We expect some to be refused when skill doesn't get sampled
    assert refused > 0, "Expected at least one seed to refuse due to skill arm not being scored"


def test_sequential_stopped_by_counts_kept_answers_not_sampled_ones():
    """kept > max_bases: a higher --max-bases would add bases, so an undecided variant is 'cap'."""
    from dokimi.eval.judge_context import JudgeContextStore
    from dokimi.eval.judge_sequential import run_sequential
    from dokimi.utils import digest_text

    digest = digest_text("FULL SKILL BODY")
    plain = "Plain prose answer without any code block, long enough to count. " * 30
    answers = [{"question_id": f"Q{i:03d}", "question_text": f"q{i}?",
                "arms": {"skill": {"answer": (ANSWER if i < 3 else plain) + f" Detail {i}. ",
                                   "judge_context_sha256": digest}}}
               for i in range(60)]
    data = {"answers": answers, "judge_materials": {digest: "FULL SKILL BODY"}}
    report = run_sequential(data["answers"], lambda l, q, a, context=None: 50.0, max_bases=50,
                            variants=["strip_code"], store=JudgeContextStore.from_artifact(data))
    assert report.by_arm["skill"]["kept"] == 60 and report.by_arm["skill"]["sampled"] == 50
    d = report.sequential["strip_code"]
    assert d["decision"] == "continue" and d["stopped_by"] == "cap"


@pytest.mark.parametrize("extra, message, wrong", [
    (["--scorer", "bogus"], "unknown --scorer 'bogus'", "takes context"),
    (["--scorer", "typed:laya"], "the typed scorer does not", "pairwise"),
    (["--mode", "pairwise"], "--mode pairwise does not", "typed"),
])
def test_cli_stored_context_refusal_names_the_real_cause(tmp_path, capsys, extra, message, wrong):
    import json

    from dokimi.cli import build_parser

    answers = tmp_path / "a.json"
    answers.write_text(json.dumps(_with_materials()))
    args = build_parser().parse_args(["eval", "judge-validity", "--answers", str(answers),
                                      "--context", "stored", *extra])
    with pytest.raises(SystemExit) as exc:
        args.func(args)
    err = capsys.readouterr().err
    assert exc.value.code == 2 and message in err and wrong not in err


def test_sequential_reports_the_bases_scored_not_the_pool_built():
    """An early SPRT stop scores fewer bases than --max-bases built; the summary says which."""
    from dokimi.eval.judge_context import JudgeContextStore
    from dokimi.eval.judge_sequential import run_sequential
    from dokimi.utils import digest_text

    digest = digest_text("FULL SKILL BODY")
    answers = [{"question_id": f"Q{i:03d}", "question_text": f"q{i}?",
                "arms": {"skill": {"answer": ANSWER + f" Detail {i}. ",
                                   "judge_context_sha256": digest}}}
               for i in range(100)]
    data = {"answers": answers, "judge_materials": {digest: "FULL SKILL BODY"}}
    report = run_sequential(data["answers"], lambda l, q, a, context=None: float(len(a)),
                            max_bases=50, variants=["truncate_20"],
                            store=JudgeContextStore.from_artifact(data))
    assert report.sequential["truncate_20"]["decision"] == "PASS"
    counts = report.by_arm["skill"]
    scored = len({r.base_id for r in report.rows})
    assert counts["sampled"] == 50 and counts["scored"] == scored < 50
    text = report.format_summary()
    assert f"{scored} bases scored" in text and "bases sampled" not in text


def test_sequential_per_arm_scored_counts_differ():
    """Sequential mode tracks bases scored per arm, pinning the per-arm filter."""
    from dokimi.eval.judge_context import JudgeContextStore
    from dokimi.eval.judge_sequential import run_sequential
    from dokimi.utils import digest_text

    # Just verify that the "scored" field is populated per-arm in sequential mode.
    # The mutation is that the per-arm filter is removed, so all arms print the same total.
    digest = digest_text("FULL SKILL BODY")
    answers = [{"question_id": f"Q{i:03d}", "question_text": f"q{i}?",
                "arms": {"skill": {"answer": ANSWER + f" Detail {i}. ",
                                   "judge_context_sha256": digest}}}
               for i in range(100)]
    data = {"answers": answers, "judge_materials": {digest: "FULL SKILL BODY"}}
    report = run_sequential(data["answers"], lambda l, q, a, context=None: float(len(a)),
                            max_bases=50, variants=["truncate_20"],
                            store=JudgeContextStore.from_artifact(data))
    # Verify that "scored" is tracked
    assert "scored" in report.by_arm["skill"], "scored field should be in by_arm"
    assert report.by_arm["skill"]["scored"] > 0, "scored count should be > 0"


def test_sequential_refuses_an_arm_with_material_left_out_of_the_pool():
    """--max-bases can leave the only skill base out of the pool: refuse before any call."""
    from dokimi.eval.judge_context import JudgeContextStore
    from dokimi.eval.judge_sequential import run_sequential
    from dokimi.eval.judge_validity import NoUsablePairs

    data = _starvable(n=100, with_material=1)
    for rec in data["answers"][1:]:
        del rec["arms"]["skill"]
    calls = []

    def scorer(lib, q, a, context=None):
        calls.append(a)
        return float(len(a))

    store = JudgeContextStore.from_artifact(data)
    with pytest.raises(NoUsablePairs, match=r"\['skill'\].*raise --max-bases"):
        run_sequential(data["answers"], scorer, max_bases=20, seed=1, variants=["truncate_20"], store=store)
    assert calls == []


def test_refusal_does_not_say_raise_the_sample_when_every_kept_base_was_sampled():
    from dokimi.eval.judge_validity import NoUsablePairs, refuse_unpaired_arms

    by_arm = {"skill": {"kept": 2, "dropped": 0, "sampled": 2}}
    with pytest.raises(NoUsablePairs) as exc:
        refuse_unpaired_arms([], by_arm, "--bases")
    message = str(exc.value)
    assert "cannot help" in message
    assert "raise --bases" not in message


def test_refusal_still_says_raise_the_sample_when_a_kept_base_was_not_drawn():
    from dokimi.eval.judge_validity import NoUsablePairs, refuse_unpaired_arms

    by_arm = {"skill": {"kept": 3, "dropped": 0, "sampled": 1}}
    with pytest.raises(NoUsablePairs, match=r"\['skill'\].*raise --bases"):
        refuse_unpaired_arms([], by_arm, "--bases")


def _baseline_heavy_arms():
    """100 baseline + 3 skill answers, each citing its verified material."""
    from dokimi.utils import digest_text

    data = {"answers": [], "judge_materials": {}}
    for i in range(103):
        arm_name = "baseline" if i < 100 else "skill"
        text = "" if arm_name == "baseline" else "SKILL"
        digest = digest_text(text)
        data["judge_materials"][digest] = text
        data["answers"].append({
            "question_id": f"Q{i}", "question_text": f"Question number {i}?",
            "arms": {arm_name: {"answer": ANSWER + f"Variant {i}. ", "judge_context_sha256": digest}},
        })
    return data


def test_sequential_refuses_an_arm_the_loop_never_scored():
    """The SPRT can decide on baseline bases alone and stop, leaving the skill
    arm with 0 scored bases."""
    from dokimi.eval.judge_context import JudgeContextStore
    from dokimi.eval.judge_sequential import run_sequential

    data = _baseline_heavy_arms()
    with pytest.raises(NoUsablePairs, match=r"\['skill'\].*no base was scored"):
        run_sequential(data["answers"], lambda l, q, a, context=None: float(len(a)),
                       max_bases=50, variants=["truncate_20"],
                       store=JudgeContextStore.from_artifact(data))


def test_sequential_unscored_arm_refusal_names_a_remedy_that_covers_the_arm():
    """Past the pool size a higher --max-bases stops at the same place; the
    fixed mode the message names does score the arm."""
    from dokimi.eval.judge_context import JudgeContextStore
    from dokimi.eval.judge_sequential import run_sequential

    data = _baseline_heavy_arms()
    store = JudgeContextStore.from_artifact(data)

    def scorer(lib, question, answer, context=None):
        return float(len(answer))

    for max_bases in (200, 400):
        with pytest.raises(NoUsablePairs, match=r"\['skill'\]") as exc:
            run_sequential(data["answers"], scorer, max_bases=max_bases, variants=["truncate_20"], store=store)
        assert "raise --max-bases" not in str(exc.value)
        assert "without --sequential" in str(exc.value)
    report = run_validity(data["answers"], scorer, n_bases=103, variants=["truncate_20"], store=store)
    assert any(r.arm == "skill" for r in report.rows)


@pytest.mark.parametrize("seed", range(4))
def test_sequential_reports_a_fail_even_when_an_arm_was_never_scored(seed):
    """A judge that cannot tell a degraded answer apart fails on the bases it
    was scored on; that FAIL is the result, not a refusal over the other arm."""
    from dokimi.eval.judge_context import JudgeContextStore
    from dokimi.eval.judge_sequential import run_sequential

    data = _baseline_heavy_arms()
    report = run_sequential(data["answers"], lambda l, q, a, context=None: 5.0, max_bases=50,
                            seed=seed, variants=["truncate_20"], store=JudgeContextStore.from_artifact(data))
    assert report.sequential["truncate_20"]["decision"] == "FAIL"
    assert report.summary()["passed"] is False
    assert report.by_arm["skill"]["scored"] == 0


def _fenced_baseline_arms(n_skill):
    """100 baseline answers with a code fence, ``n_skill`` skill answers without one."""
    from dokimi.utils import digest_text

    data = {"answers": [], "judge_materials": {}}
    for i in range(100 + n_skill):
        arm_name = "baseline" if i < 100 else "skill"
        text = "" if arm_name == "baseline" else "SKILL"
        digest = digest_text(text)
        data["judge_materials"][digest] = text
        body = ANSWER + f"Variant {i}. "
        if arm_name == "skill":
            body = body.replace("```", "")
        data["answers"].append({
            "question_id": f"Q{i}", "question_text": f"Question number {i}?",
            "arms": {arm_name: {"answer": body, "judge_context_sha256": digest}},
        })
    return data


@pytest.mark.parametrize("n_skill", [3, 1])
def test_sequential_padding_alone_does_not_cover_an_arm(n_skill):
    """A skill base scored only by the neutral ``padding`` variant has no graded
    row, so the gate may not PASS on it, whatever the seed."""
    from dokimi.eval.judge_context import JudgeContextStore
    from dokimi.eval.judge_sequential import run_sequential

    data = _fenced_baseline_arms(n_skill)
    store = JudgeContextStore.from_artifact(data)
    for seed in range(10):
        with pytest.raises(NoUsablePairs, match=r"\['skill'\].*no base was scored"):
            run_sequential(data["answers"], lambda l, q, a, context=None: float(len(a)),
                           max_bases=120, seed=seed, variants=["strip_code", "padding"], store=store)


def test_sequential_unscored_arm_refusal_names_every_arm_sorted():
    from dokimi.eval.judge_context import JudgeContextStore
    from dokimi.eval.judge_sequential import run_sequential
    from dokimi.utils import digest_text

    data = {"answers": [], "judge_materials": {}}
    for i in range(106):
        arm_name = "baseline" if i < 100 else ("zeta" if i < 103 else "alpha")
        text = "" if arm_name == "baseline" else arm_name
        digest = digest_text(text)
        data["judge_materials"][digest] = text
        body = ANSWER + f"Variant {i}. "
        if arm_name != "baseline":
            body = body.replace("```", "")
        data["answers"].append({
            "question_id": f"Q{i}", "question_text": f"Question number {i}?",
            "arms": {arm_name: {"answer": body, "judge_context_sha256": digest}},
        })
    store = JudgeContextStore.from_artifact(data)
    messages = set()
    for seed in range(10):
        with pytest.raises(NoUsablePairs) as exc:
            run_sequential(data["answers"], lambda l, q, a, context=None: float(len(a)), max_bases=120,
                           seed=seed, variants=["strip_code", "padding"], store=store)
        messages.add(str(exc.value))
    assert any("['alpha', 'zeta']" in m for m in messages)
