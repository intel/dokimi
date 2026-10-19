# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Tests for Judge."""

import re
import sys
import types
import pytest
import json
from unittest.mock import Mock, patch

# Mock langchain
for mod_name in ['langchain_openai', 'langchain_anthropic']:
    if mod_name not in sys.modules:
        mock_mod = types.ModuleType(mod_name)
        mock_mod.ChatOpenAI = Mock
        mock_mod.ChatAnthropic = Mock
        sys.modules[mod_name] = mock_mod

from dokimi.eval.judge import Judge, JUDGE_PROMPT, JUDGE_PROMPT_GROUNDED


@pytest.fixture
def sample_answers():
    return [
        {
            "question_id": "q_001",
            "question_text": "How to use parallel_for?",
            "library_name": "oneTBB",
            "with_docs": {
                "answer": "Use tbb::parallel_for(range, lambda)...",
                "retrieved_docs": [{"snippet": "Doc about parallel_for"}],
                "model": "gpt-4o"
            },
            "without_docs": {
                "answer": "You can use parallel loops...",
                "model": "gpt-4o"
            }
        }
    ]


@pytest.fixture
def mock_judge_response():
    return json.dumps({
        "correctness": 85,
        "completeness": 90,
        "specificity": 80,
        "code_quality": 85,
        "actionability": 90,
        "aggregate": 86,
        "reasoning": {
            "correctness": "Accurate",
            "completeness": "Complete",
            "specificity": "Specific to oneTBB",
            "code_quality": "Good code",
            "actionability": "Clear steps"
        }
    })


@pytest.fixture
def mock_llm(mock_judge_response):
    llm = Mock()
    resp = Mock()
    resp.content = mock_judge_response
    llm.invoke.return_value = resp
    return llm


class TestJudgeInit:
    def test_init_openai(self):
        mock_cls = Mock()
        with patch('dokimi.eval.judge.ChatOpenAI', mock_cls, create=True), \
             patch('dokimi.eval.judge.LANGCHAIN_AVAILABLE', True):
            judge = Judge(model="gpt-4o", provider="openai")
            assert judge.model == "gpt-4o"
            assert judge.provider == "openai"
    
    def test_init_anthropic(self):
        mock_cls = Mock()
        with patch('dokimi.eval.judge.ChatAnthropic', mock_cls, create=True), \
             patch('dokimi.eval.judge.LANGCHAIN_AVAILABLE', True):
            judge = Judge(model="claude-sonnet", provider="anthropic", api_key="test")
            assert judge.model == "claude-sonnet"
            assert judge.provider == "anthropic"
    
    def test_init_unsupported_provider(self):
        mock_cls = Mock()
        with patch('dokimi.eval.judge.ChatOpenAI', mock_cls, create=True), \
             patch('dokimi.eval.judge.LANGCHAIN_AVAILABLE', True):
            with pytest.raises(ValueError) as exc:
                Judge(provider="unsupported")
            assert "Unsupported provider" in str(exc.value)
    
    def test_init_no_langchain(self):
        with patch('dokimi.eval.judge.LANGCHAIN_AVAILABLE', False):
            with pytest.raises(ImportError):
                Judge()


class TestEvaluateAnswers:
    def test_returns_list(self, sample_answers, mock_llm):
        mock_cls = Mock(return_value=mock_llm)
        with patch('dokimi.eval.judge.ChatAnthropic', mock_cls, create=True), \
             patch('dokimi.eval.judge.LANGCHAIN_AVAILABLE', True):
            judge = Judge()
            evals = judge.evaluate_answers("oneTBB", sample_answers)
            
            assert isinstance(evals, list)
            assert len(evals) == len(sample_answers)
    
    def test_includes_required_fields(self, sample_answers, mock_llm):
        mock_cls = Mock(return_value=mock_llm)
        with patch('dokimi.eval.judge.ChatAnthropic', mock_cls, create=True), \
             patch('dokimi.eval.judge.LANGCHAIN_AVAILABLE', True):
            judge = Judge()
            evals = judge.evaluate_answers("oneTBB", sample_answers)
            
            for e in evals:
                assert "question_id" in e
                assert "question_text" in e
                assert "with_docs" in e
                assert "without_docs" in e
                assert "delta" in e
    
    def test_calculates_delta(self, sample_answers, mock_llm):
        # Mock different scores for WITH and WITHOUT
        responses = [
            json.dumps({"correctness": 90, "completeness": 90, "specificity": 85, "code_quality": 90, "actionability": 90, "aggregate": 89, "reasoning": {}}),
            json.dumps({"correctness": 70, "completeness": 65, "specificity": 50, "code_quality": 60, "actionability": 65, "aggregate": 62, "reasoning": {}})
        ]
        mock_llm.invoke.side_effect = [Mock(content=r) for r in responses]
        
        mock_cls = Mock(return_value=mock_llm)
        with patch('dokimi.eval.judge.ChatAnthropic', mock_cls, create=True), \
             patch('dokimi.eval.judge.LANGCHAIN_AVAILABLE', True):
            judge = Judge()
            evals = judge.evaluate_answers("oneTBB", sample_answers)
            
            # Deltas come from the recomputed aggregates, not the 89/62 the
            # model asserted: (90*2+90+90+90)/5 = 90.0 and (70*2+65+60+65)/5 = 66.0.
            assert evals[0]["delta"] == 24.0
    
    def test_error_handling(self, sample_answers):
        mock_llm = Mock()
        mock_llm.invoke.side_effect = Exception("Judge error")
        mock_cls = Mock(return_value=mock_llm)
        
        with patch('dokimi.eval.judge.ChatAnthropic', mock_cls, create=True), \
             patch('dokimi.eval.judge.LANGCHAIN_AVAILABLE', True):
            judge = Judge()
            evals = judge.evaluate_answers("oneTBB", sample_answers)
            
            assert len(evals) == 1
            assert "error" in evals[0]


class TestJudgeAnswer:
    def test_parses_scores(self, mock_llm):
        mock_cls = Mock(return_value=mock_llm)
        with patch('dokimi.eval.judge.ChatAnthropic', mock_cls, create=True), \
             patch('dokimi.eval.judge.LANGCHAIN_AVAILABLE', True):
            judge = Judge()
            result = judge._judge_answer("oneTBB", "Q?", "A.", "context")
            
            assert result["correctness"] == 85
            # 86 is what the mocked model *said*; the aggregate is recomputed
            # from the dimensions it scored: (85*2 + 90 + 85 + 90) / 5.
            assert result["aggregate"] == 87.0
            assert "reasoning" in result

    def test_aggregate_ignores_what_the_model_claimed(self, mock_llm):
        """A judge that scores every dimension 50 cannot report 100."""
        mock_llm.invoke.return_value.content = json.dumps({
            "correctness": 50, "completeness": 50, "specificity": 50,
            "code_quality": 50, "actionability": 50, "aggregate": 100,
        })
        mock_cls = Mock(return_value=mock_llm)
        with patch('dokimi.eval.judge.ChatAnthropic', mock_cls, create=True), \
             patch('dokimi.eval.judge.LANGCHAIN_AVAILABLE', True):
            result = Judge()._judge_answer("oneTBB", "Q?", "A.", "context")
        assert result["aggregate"] == 50.0

    def test_specificity_does_not_move_the_aggregate(self, mock_llm):
        """It is reported as a diagnostic and carries no weight.

        An answer that names dense, correctly-shaped identifiers which are all
        wrong for the library is the `wrong_tools` degradation, and weighting
        specificity rewards it for the vocabulary it kept.
        """
        base = {"correctness": 40, "completeness": 60,
                "code_quality": 70, "actionability": 80}
        got = []
        mock_cls = Mock(return_value=mock_llm)
        with patch('dokimi.eval.judge.ChatAnthropic', mock_cls, create=True), \
             patch('dokimi.eval.judge.LANGCHAIN_AVAILABLE', True):
            judge = Judge()
            for spec in (0, 100):
                mock_llm.invoke.return_value.content = json.dumps(
                    {**base, "specificity": spec, "aggregate": 0})
                result = judge._judge_answer("oneTBB", "Q?", "A.", "context")
                assert result["specificity"] == spec
                got.append(result["aggregate"])
        assert got[0] == got[1] == 58.0  # (40*2 + 60 + 70 + 80) / 5

    def test_correctness_carries_double_weight(self, mock_llm):
        """Correctness is the only dimension that punishes a wrong answer."""
        mock_cls = Mock(return_value=mock_llm)
        dims = ("correctness", "completeness", "code_quality", "actionability")
        moved = {}
        with patch('dokimi.eval.judge.ChatAnthropic', mock_cls, create=True), \
             patch('dokimi.eval.judge.LANGCHAIN_AVAILABLE', True):
            judge = Judge()
            for dim in dims:
                scores = {d: 100 for d in dims}
                scores[dim] = 0
                mock_llm.invoke.return_value.content = json.dumps(
                    {**scores, "specificity": 100, "aggregate": 0})
                moved[dim] = judge._judge_answer("oneTBB", "Q?", "A.", "context")["aggregate"]
        assert moved["correctness"] == 60.0
        assert moved["completeness"] == moved["code_quality"] == moved["actionability"] == 80.0

    def _judge(self, mock_llm, claims, correctness):
        mock_llm.invoke.return_value.content = json.dumps({
            "numeric_claims": claims, "correctness": correctness, "completeness": 90,
            "specificity": 90, "code_quality": 90, "actionability": 90, "aggregate": 0,
        })
        mock_cls = Mock(return_value=mock_llm)
        with patch('dokimi.eval.judge.ChatAnthropic', mock_cls, create=True), \
             patch('dokimi.eval.judge.LANGCHAIN_AVAILABLE', True):
            return Judge()._judge_answer("oneTBB", "Q?", "A.", "context")

    @pytest.mark.parametrize("claims, correctness, expected", [
        (["64 B line: right", "10 ns latency: wrong"], 95, 40),
        (["10 ns latency: Wrong."], 95, 40),
        (["64 bytes: wrong (should be 128 bytes)"], 95, 40),
        (["64 bytes: wrong for this CPU"], 95, 40),
        (["64 bytes: incorrect"], 95, 40),
        (["64 bytes - wrong"], 95, 40),
        (["64 bytes (wrong)"], 95, 40),
        ([{"figure": "64 bytes", "verdict": "wrong"}], 95, 40),
        (["strides (8,) or (8, 8000): partially wrong"], 95, 40),
        (["64 bytes: mostly incorrect"], 95, 40),
        # An item with no readable verdict does not stop the cap on another.
        ([7, None, {"figure": "x"}, "64 bytes: wrong"], 95, 40),
        (["10 ns latency: wrong"], 25, 25),   # already under the cap
        (["10 ns latency: wrong"], 40, 40),
        (["64 B line: right"], 95, 95),
        (["wrong-way ratio 2: right"], 95, 95),
        (["64 bytes: right (not wrong)"], 95, 95),
        (["the wrong figure is 64 bytes"], 95, 95),
        ([{"figure": "64 bytes", "verdict": "right"}], 95, 95),
        (["64 bytes"], 95, 95),               # no verdict at all
        (["64 bytes: wrongly stated"], 95, 95),
        (["64 bytes: correct"], 95, 95),
        (["64 bytes: partially right"], 95, 95),
        (["64 bytes, non-wrong"], 95, 95),
        ("none", 95, 95),                      # not a list: nothing to enforce
        # From real Haiku judgements: a qualifier list ending in the verdict is read.
        (["one execution port: misleading/wrong"], 95, 40),
        (["reads <270% of operations: ambiguous/wrong"], 95, 40),
        (["~160x AVX: speculative/wrong"], 95, 40),
        # The right-hand counterparts read as right and do not cap.
        (["expected throughput 4-8x: conditional/right"], 95, 95),
        (["4.5x speedup threshold: reasonable, not wrong"], 95, 95),
        (["SIMD_SORT_MIN_N = 32: reasonable heuristic (not wrong)"], 95, 95),
        (["p < 0.05 threshold: standard statistical convention, correct"], 95, 95),
        # Must NOT cap: the judge says the answer flagged a bad claim correctly.
        (["2x speedup: correctly marked as WRONG to claim"], 95, 95),
        (["20x faster (example of wrong claim): right (correctly marked as WRONG)"], 95, 95),
        (["TILE 64: right (reasonable choice, not factually wrong)"], 95, 95),
        (["500 ns threshold: contextual (neither universally right nor wrong)"], 95, 95),
        # The answer's figure is the wrong one; the right one is the correction.
        (["8 ms: wrong -> corrected: right"], 95, 40),
        # A "/" or "," in the figure is not a verdict: the verdict after the colon is read.
        (["left/right shift by 3: wrong"], 95, 40),
        (["4 KiB, correct alignment assumed: wrong"], 95, 40),
        (["TILE 64 (not wrong): wrong"], 95, 40),
        (["note, wrong figure: right"], 95, 95),
        # "not entirely wrong" is a hedge: no cap, and not read as right either.
        (["8 ms: not entirely wrong"], 95, 95),
        (["8 ms: not right"], 95, 40),
        # The fallback is case-insensitive and handles slash-separated qualifiers and multiple separators.
        (["x (misleading/wrong)"], 95, 40),
        (["x - misleading/wrong"], 95, 40),
        (["x — misleading/wrong"], 95, 40),  # em-dash
        (["x: misleading/WRONG"], 95, 40),
        # The fallback handles trailing commas, adverbs, "incorrect", and negations.
        (["x: dubious, partially wrong"], 95, 40),
        (["x: misleading/incorrect"], 95, 40),
        (["x: dubious, not correct"], 95, 40),
        (["x - not wrong"], 95, 95),
        # Case-insensitive, trailing punctuation and whitespace, negation, en and em dash.
        (["x: Not Right"], 95, 40),
        (["x: misleading/wrong."], 95, 40),
        (["64 B: WRONG (expected 128)"], 95, 40),
        (["x — not wrong"], 95, 95),
        # A single-token qualifier caps.
        (["x: dubious, misleading/wrong"], 95, 40),
        # A claim with no real separator (`:`, `-`, `(`) is unread, even if `,` appears.
        (["64 bytes, wrong"], 95, 95),
        # Qualified verdicts work the same in dict claims and strings.
        ([{"figure": "x", "verdict": "misleading/wrong"}], 95, 40),
        ([{"figure": "x", "verdict": "reasonable, not wrong"}], 95, 95),
        # Intensifiers strengthen the verdict; the en dash, a figure-like qualifier and a
        # trailing space still read.
        (["x: dubious, entirely wrong"], 95, 40),
        (["8 ms: dubious, entirely wrong"], 95, 40),
        (["x: dubious, completely wrong"], 95, 40),
        # A negated intensified "correct" is wrong: negation resolves before the intensifier.
        (["x: dubious, not entirely correct"], 95, 40),
        (["x: dubious, not partially correct"], 95, 40),
        # An intensified "correct" is right, so it does not cap.
        (["x: dubious, entirely correct"], 95, 95),
        (["x \u2013 misleading/wrong"], 95, 40),
        (["x: L2/wrong"], 95, 40),
        (["x: misleading/wrong "], 95, 40),
        # Intensifiers, a figure-like qualifier, and a verdict followed by text (read only by
        # _VERDICT, not by the end-anchored fallback).
        (["x: dubious, mostly wrong"], 95, 40),
        (["x: dubious, largely wrong"], 95, 40),
        (["x: dubious, totally wrong"], 95, 40),
        (["x: v1.2/wrong"], 95, 40),
        (["x (wrong, expected 128)"], 95, 40),
        (["x: partially wrong (expected 128)"], 95, 40),
        # Negated incorrect: flips to right, does not cap.
        (["x: dubious, not incorrect"], 95, 95),
        # Case insensitivity on intensifiers and multi-char-class qualifiers.
        (["x: dubious, Entirely wrong"], 95, 40),
        (["x: C++/wrong"], 95, 40),
        (["x: AVX-512/wrong"], 95, 40),
        (["x (misleading/wrong)."], 95, 40),
        # Hedged verdicts: a question mark leaves wrong unread.
        (["x: dubious, wrong?"], 95, 95),
        # Figures with separators: the end-anchored pattern finds the real verdict.
        (["ratio 3:1, correct alignment assumed: misleading/wrong"], 95, 40),
        (["ratio 3:1, correct alignment assumed: not right"], 95, 40),
        (["latency 4 cycles (L1, correct for Skylake): misleading/wrong"], 95, 40),
        (["f(x) = 4, correct rounding: misleading/wrong"], 95, 40),
        (["64 - 128 B, correct range: misleading/wrong"], 95, 40),
    ])
    def test_a_wrong_figure_caps_correctness_at_forty(self, mock_llm, claims, correctness, expected):
        """The prompt promises "a single wrong figure caps it at 40". A reply that
        lists a wrong figure and still scores 95 breaks that promise, so the cap is
        applied here and not left to the model."""
        result = self._judge(mock_llm, claims, correctness)
        assert result["correctness"] == expected
        assert result["aggregate"] == round((expected * 2 + 90 * 3) / 5, 1)
        if expected < correctness:
            assert result["caps"] == {"correctness": {"judged": float(correctness), "rule": "wrong_figure"}}
        else:
            assert "caps" not in result

    def test_a_claim_with_no_verdict_is_recorded_not_logged(self, mock_llm, caplog):
        # The reply is parsed twice per attempt (once by the cache check), so a
        # warning would repeat for every hedged verdict.
        with caplog.at_level("WARNING", logger="dokimi.eval.judge"):
            result = self._judge(mock_llm, ["64 bytes", "10 ns: uncertain"], 95)
        assert result["unread_claims"] == [0, 1]
        assert caplog.text == ""

    @pytest.mark.parametrize("claims, unread", [
        (["64 bytes", "8 ms: uncertain", "4 B: right", 7, None], [0, 1, 3, 4]),
        (["10 ns: wrong", "8 ms: unverified"], [1]),
    ])
    def test_a_claim_with_no_verdict_is_counted(self, mock_llm, claims, unread):
        assert self._judge(mock_llm, claims, 95)["unread_claims"] == unread

    @pytest.mark.parametrize("claim", [
        "expected throughput 4-8x: conditional/right",
        "4.5x speedup threshold: reasonable, not wrong",
        "SIMD_SORT_MIN_N = 32: reasonable heuristic (not wrong)",
        "p < 0.05 threshold: standard statistical convention, correct",
        "x: dubious, not incorrect",
        "x - not wrong",
        "x — not wrong",
        "x \u2013 not wrong",
    ])
    def test_a_qualified_or_negated_verdict_is_read(self, mock_llm, claim):
        """Qualified and negated verdicts: slash-separated qualifiers, trailing commas, and `not` all read correctly."""
        assert "unread_claims" not in self._judge(mock_llm, [claim], 95)

    @pytest.mark.parametrize("claim", [
        "left/right shift by 3: dubious",
        "8 ms: not entirely wrong",
        "8 ms: not necessarily right",
        "AVX2 (256-bit, correct width): dubious",
        "x: see docs/correct-usage",
        # A "/" inside prose or a path is not a qualifier list: only a single token right after
        # the separator or comma qualifies the verdict.
        "x: see docs/correct",
        "x: unverified (see docs/right)",
        "x: unverified, matches the spec/correct",
        "x: cf. AVX/correct",
        # A non-intensifier adverb hedges the fallback verdict, as `hardly` does.
        "x: dubious, typically correct",
        "x: plausible, typically correct",
        # A path-like tail is not a verdict.
        "x: see docs/wrong",
        "x: unverified (see docs/wrong)",
        "64 B: unverified, see section 4.2/wrong",
        # False positives that must stay unread with the end-anchored pattern.
        "TILE (default, wrong on ARM): unverified",
        "buffer 64 B - 128 B, wrong alignment assumed: unverified",
        "2x: correctly identified, wrong claim by user",
        "x: depends, wrong if cached",
        "x: see /wrong/path",
        # Adverbs and slash/comma alternatives that must stay unread.
        "x: see /wrong",
        "x: unverified, right/wrong",
        "x: unverified, right, wrong",
        "x: dubious, hardly wrong",
        "x: unverified, wrong/right",
        "x: unverified, wrong, right",
        "x: unverified, correct/wrong",
        "x: unverified, correct, wrong",
        "x: unverified, incorrect/wrong",
        "x: dubious, barely wrong",
        "x: dubious, scarcely wrong",
        "x: dubious, seemingly right",
        "x: unverified, possibly wrong",
        "x: dubious, nearly wrong",
        "x: unverified, formerly correct",
        "x: non-wrong",
        "x: semi-correct",
        "x: misleading/wrong!",
        # Multi-line claims: the pattern requires the verdict to end the string, not at a line break.
        "TILE (default, wrong\non ARM): unverified",
        "AVX2 (256-bit, correct\nwidth): dubious",
    ])
    def test_a_figure_with_a_slash_or_a_hedge_is_not_read_as_right(self, mock_llm, claim):
        assert self._judge(mock_llm, [claim], 95)["unread_claims"] == [0]

    def test_readable_claims_record_no_unread_marker(self, mock_llm):
        assert "unread_claims" not in self._judge(mock_llm, ["4 B: right", "8 ms: wrong"], 95)

    def test_grounded_weights_grounding_like_correctness(self, mock_llm):
        """With an excerpt to check against, faithfulness to it *is* correctness."""
        mock_llm.invoke.return_value.content = json.dumps({
            "correctness": 100, "completeness": 100, "specificity": 0,
            "code_quality": 100, "actionability": 100,
            "factual_grounding": 0, "aggregate": 0,
        })
        mock_cls = Mock(return_value=mock_llm)
        with patch('dokimi.eval.judge.ChatAnthropic', mock_cls, create=True), \
             patch('dokimi.eval.judge.LANGCHAIN_AVAILABLE', True):
            result = Judge()._judge_answer(
                "oneTBB", "Q?", "A.", "context", ground_truth="excerpt")
        # (100*2 + 100 + 100 + 100 + 0*2) / 7 — specificity=0 is not in the mean
        assert result["aggregate"] == 71.4
        assert result["grounded"] is True


    def test_invalid_json_raises(self, mock_llm):
        mock_llm.invoke.return_value.content = "Not JSON"
        mock_cls = Mock(return_value=mock_llm)
        
        with patch('dokimi.eval.judge.ChatAnthropic', mock_cls, create=True), \
             patch('dokimi.eval.judge.LANGCHAIN_AVAILABLE', True):
            judge = Judge()
            with pytest.raises(ValueError):
                judge._judge_answer("oneTBB", "Q?", "A.", "context")
    
    def test_non_numeric_dimension_is_retried(self, mock_llm, mock_judge_response):
        bad = Mock(content=mock_judge_response.replace('"code_quality": 85', '"code_quality": "N/A"'))
        good = Mock(content=mock_judge_response)
        mock_llm.invoke.side_effect = [bad, good]
        mock_cls = Mock(return_value=mock_llm)

        with patch('dokimi.eval.judge.ChatAnthropic', mock_cls, create=True), \
             patch('dokimi.eval.judge.LANGCHAIN_AVAILABLE', True):
            scores = Judge()._judge_answer("oneTBB", "Q?", "A.", "context")
        assert mock_llm.invoke.call_count == 2
        assert scores["code_quality"] == 85

    def test_missing_keys_raises(self, mock_llm):
        mock_llm.invoke.return_value.content = json.dumps({"correctness": 85})  # Missing other keys
        mock_cls = Mock(return_value=mock_llm)
        
        with patch('dokimi.eval.judge.ChatAnthropic', mock_cls, create=True), \
             patch('dokimi.eval.judge.LANGCHAIN_AVAILABLE', True):
            judge = Judge()
            with pytest.raises(ValueError) as exc:
                judge._judge_answer("oneTBB", "Q?", "A.", "context")
            assert "Missing keys" in str(exc.value)


class TestFormatContext:
    def test_empty_docs(self):
        result = Judge._format_context([])
        assert result == "(No documentation retrieved)"
    
    def test_formats_docs(self):
        docs = [
            {"snippet": "Doc 1 content"},
            {"snippet": "Doc 2 content"}
        ]
        result = Judge._format_context(docs)
        assert "Doc 1:" in result
        assert "Doc 2:" in result


class TestSaveLoadEvaluations:
    def test_save_evaluations(self, tmp_path, sample_answers, mock_llm):
        mock_cls = Mock(return_value=mock_llm)
        with patch('dokimi.eval.judge.ChatAnthropic', mock_cls, create=True), \
             patch('dokimi.eval.judge.LANGCHAIN_AVAILABLE', True):
            judge = Judge()
            evals = judge.evaluate_answers("oneTBB", sample_answers)
            
            output_path = tmp_path / "eval.json"
            judge.save_evaluations(evals, output_path)
            
            assert output_path.exists()
            data = json.loads(output_path.read_text())
            assert "evaluated_at" in data
            assert "judge_model" in data
            assert "evaluations" in data
    
    def test_load_evaluations(self, tmp_path):
        test_data = {
            "evaluated_at": "2026-02-21T00:00:00Z",
            "judge_model": "claude-sonnet",
            "evaluations": [{"question_id": "q_001"}]
        }
        
        input_path = tmp_path / "eval.json"
        input_path.write_text(json.dumps(test_data))
        
        loaded = Judge.load_evaluations(input_path)
        assert loaded["judge_model"] == "claude-sonnet"
        assert len(loaded["evaluations"]) == 1


class TestPromptTemplate:
    def test_has_placeholders(self):
        # Prompt now uses __PLACEHOLDER__ style
        required = ["__QUESTION__", "__ANSWER__", "__CONTEXT__", "__LIBRARY_NAME__"]
        for placeholder in required:
            assert placeholder in JUDGE_PROMPT

    @pytest.mark.parametrize("prompt", [JUDGE_PROMPT, JUDGE_PROMPT_GROUNDED])
    def test_output_format_shows_no_example_scores(self, prompt):
        """Example scores anchor the judge — it returns them verbatim.

        A prompt that demonstrates 85/90/80 gets those exact values back for
        most answers, which collapses the scale to a handful of values and makes
        arm deltas meaningless. The output template must use N placeholders.
        """
        template = prompt[prompt.index("**Output format"):]
        offenders = re.findall(r'"(\w+)":\s*(\d+)', template)
        # aggregate=0 in the grounded prompt is an instruction (Python computes
        # it), not a score example.
        assert [(k, v) for k, v in offenders if (k, v) != ("aggregate", "0")] == []
        assert '"correctness": N' in template

    @pytest.mark.parametrize("prompt", [JUDGE_PROMPT, JUDGE_PROMPT_GROUNDED])
    def test_asks_the_judge_to_check_the_answer_s_numbers(self, prompt):
        """Wrong magnitudes were invisible to every judge measured.

        The `wrong_numbers` degradation scales figures by 10x and swaps units,
        leaving the prose and the length untouched; the judge scored 0.60 on it
        (four seeds, 156 pairs) and barely moved the score at all. The criterion
        alone did not lift it; criterion plus enumeration did, to 0.71 on those same
        pairs, though the two were not compared head-to-head. Both halves live on
        `correctness`, which already carries double weight, and state one cap.
        """
        criteria = " ".join(prompt[:prompt.index("**Output format")].split())
        assert "Check the answer's *numbers*" in criteria   # the criterion
        assert "copy out every figure" in criteria          # the enumeration step
        assert re.findall(r"\bcaps (?:correctness|it) at (\d+)", criteria) == ["40", "40"]

    @pytest.mark.parametrize("prompt", [JUDGE_PROMPT, JUDGE_PROMPT_GROUNDED])
    def test_the_enumeration_is_written_out_before_any_score(self, prompt):
        """`numeric_claims` comes first in the output, so the figures are on
        the page before correctness is scored, and every stored judgement shows
        whether the enumeration happened."""
        out = prompt[prompt.index("**Output format"):]
        assert '"numeric_claims"' in out
        assert out.index('"numeric_claims"') < out.index('"correctness": N')

    @pytest.mark.parametrize("prompt", [JUDGE_PROMPT, JUDGE_PROMPT_GROUNDED])
    def test_the_criterion_does_not_describe_the_gates_own_generator(self, prompt):
        """The first wording listed the exact substitutions and the 10x scaling
        `degrade.wrong_numbers` applies, so its gain was measured on the
        perturbations it described. The prompt names the class of error only.
        Measured since (fresh replication, all prompts re-drawn on the same
        pairs): the generic wording beats the listed one on the gate's own
        generator and on held-out perturbations it never describes (x3 scaling,
        unlisted unit swaps)."""
        import re

        criteria = prompt[:prompt.index("**Output format")]
        # Whitespace-normalised: the old wording wrapped "order of\n   magnitude".
        assert "order of magnitude" not in " ".join(criteria.lower().split())
        assert not re.search(r"\b10\s*[x×]", criteria)  # wrong_numbers' only edit since its named swaps went

    @pytest.mark.parametrize("prompt", [JUDGE_PROMPT, JUDGE_PROMPT_GROUNDED])
    def test_carries_the_shared_calibration_scale(self, prompt):
        """Without anchors the judge has no scale, and everything lands near 100."""
        from dokimi.eval.panel import SCORE_ANCHORS

        assert SCORE_ANCHORS in prompt
