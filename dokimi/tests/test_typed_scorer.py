# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Tests for the optional typed-decision baseline arm.

The backend is an extra, so every test here injects a fake agent: the contract
under test is the adapter (spec parsing, the gate's `Scorer` signature, the
shipped aggregate weighting, and the truncation accounting), not the model.
"""

import json

import pytest

from dokimi.eval.typed_scorer import (
    TYPED_LEVELS,
    TypedScorer,
    parse_spec,
    typed_aggregate,
    typed_scorer,
)


class FakeAgent:
    """Returns fixed level indices, and records what it was asked."""

    def __init__(self, levels=None, usage=None):
        self.levels = levels if levels is not None else {}
        self.usage = usage or {}
        self.calls = []

    def predict(self, state, questions, **kwargs):
        self.calls.append({"state": state, "questions": questions, "kwargs": kwargs})
        return {
            "answers": {
                dim: {"score": self.levels.get(dim, 3)} for dim in questions
            },
            "usage": self.usage,
        }


class TestParseSpec:
    def test_bare_backend_uses_the_default_repo(self):
        backend, repo, subfolder = parse_spec("laya")
        assert backend == "laya"
        assert repo == "convaiinnovations/laya"
        assert subfolder is None

    def test_repo_override(self):
        assert parse_spec("laya:owner/name") == ("laya", "owner/name", None)

    def test_subfolder_is_the_third_path_segment(self):
        assert parse_spec("laya:owner/name/multilingual") == (
            "laya", "owner/name", "multilingual")

    def test_unknown_backend_is_rejected(self):
        with pytest.raises(ValueError, match="unknown typed scorer backend"):
            parse_spec("gpt2")

    @pytest.mark.parametrize("target", ["/localdisk/ckpt/laya", "./models/laya-en",
                                        "../ckpt/a/b/c"])
    def test_a_local_directory_is_passed_through_whole(self, target):
        """Split the Hub way, /localdisk/ckpt/laya would load /localdisk with
        subfolder ckpt/laya."""
        assert parse_spec(f"laya:{target}") == ("laya", target, None)

    @pytest.mark.parametrize("target", [r"C:\models\x", "C:/models/x", r"\\srv\share\m"])
    def test_windows_paths_are_local_not_hub_ids(self, target):
        """`C:/models/x` used to become Hub repo `C:/models`, subfolder `x`."""
        assert parse_spec(f"laya:{target}") == ("laya", target, None)

    def test_home_relative_path_is_expanded(self):
        import os

        _, repo, sub = parse_spec("laya:~/m/x")
        assert repo == os.path.expanduser("~/m/x") and sub is None

    def test_an_existing_relative_directory_counts_as_local(self, tmp_path, monkeypatch):
        (tmp_path / "ckpts" / "laya" / "en").mkdir(parents=True)
        monkeypatch.chdir(tmp_path)
        assert parse_spec("laya:ckpts/laya/en") == ("laya", "ckpts/laya/en", None)

    def test_trailing_and_surrounding_noise_is_normalised(self):
        assert parse_spec("laya:o/n/sub/") == ("laya", "o/n", "sub")
        assert parse_spec("laya: o/n ") == ("laya", "o/n", None)

    @pytest.mark.parametrize("target", ["o//sub", "onlyone"])
    def test_a_malformed_hub_id_is_rejected(self, target):
        with pytest.raises(ValueError, match="neither a Hub id"):
            parse_spec(f"laya:{target}")


class TestAggregate:
    def test_correctness_is_weighted_twice(self):
        """Same weighting as the shipped judge, so the numbers are comparable."""
        scores = {"correctness": 100.0, "completeness": 0.0,
                  "code_quality": 0.0, "actionability": 0.0}
        assert typed_aggregate(scores) == 40.0

        scores = {"correctness": 0.0, "completeness": 100.0,
                  "code_quality": 0.0, "actionability": 0.0}
        assert typed_aggregate(scores) == 20.0

    def test_all_top_level_is_one_hundred(self):
        assert typed_aggregate({d: 100.0 for d in TYPED_LEVELS}) == 100.0


class TestScorerContract:
    def test_matches_the_gate_scorer_signature_and_scale(self):
        agent = FakeAgent()  # every dimension at the top level
        scorer = typed_scorer("laya", agent=agent)
        value = scorer("oneTBB", "How do I use parallel_for?", "Like this: ...")

        assert value == 100.0
        assert agent.calls[0]["state"].startswith("LIBRARY: oneTBB")
        assert "QUESTION: How do I use parallel_for?" in agent.calls[0]["state"]
        assert set(agent.calls[0]["questions"]) == set(TYPED_LEVELS)

    def test_worst_levels_score_zero(self):
        scorer = typed_scorer("laya", agent=FakeAgent({d: 0 for d in TYPED_LEVELS}))
        assert scorer("oneTBB", "q", "a") == 0.0

    def test_max_len_is_only_passed_when_set(self):
        agent = FakeAgent()
        typed_scorer("laya", agent=agent)("lib", "q", "a")
        assert agent.calls[0]["kwargs"] == {}

        agent = FakeAgent()
        typed_scorer("laya", agent=agent, max_len=8192)("lib", "q", "a")
        assert agent.calls[0]["kwargs"] == {"max_len": 8192}

    def test_name_reports_backend_repo_and_subfolder(self):
        assert TypedScorer("laya", agent=FakeAgent()).name == (
            "laya:convaiinnovations/laya")
        assert TypedScorer("laya:o/n/multilingual", agent=FakeAgent()).name == (
            "laya:o/n/multilingual")


class TestUsageAccounting:
    def test_truncated_states_are_counted_not_hidden(self):
        """A score produced from a prefix of the answer has to say so."""
        agent = FakeAgent(usage={"truncated": True, "state_tokens_dropped": 1600})
        scorer = TypedScorer("laya", agent=agent)
        scorer("lib", "q", "a")
        scorer("lib", "q", "a")

        assert scorer.usage["calls"] == 2
        assert scorer.usage["truncated"] == 2
        assert scorer.usage["state_tokens_dropped"] == 3200
        summary = scorer.usage_summary()
        assert "truncated states 2/2" in summary
        assert "mean state tokens dropped 1600" in summary

    def test_missing_usage_block_is_tolerated(self):
        scorer = TypedScorer("laya", agent=FakeAgent(usage=None))
        scorer("lib", "q", "a")
        assert scorer.usage["truncated"] == 0
        assert scorer.usage["state_tokens_dropped"] == 0


class TestOptionalDependency:
    def test_missing_backend_names_the_extra(self, monkeypatch):
        import builtins

        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "laya":
                raise ImportError("No module named 'laya'")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fake_import)
        with pytest.raises(ImportError, match=r"dokimi\[typed-scorer\]"):
            typed_scorer("laya")


    @staticmethod
    def _fake_laya(monkeypatch):
        import sys
        import types

        loads = []
        module = types.ModuleType("laya")
        module.load = lambda repo, **kwargs: loads.append((repo, kwargs)) or FakeAgent()
        monkeypatch.setitem(sys.modules, "laya", module)
        return loads

    @pytest.mark.parametrize("spec,expected", [
        ("laya:o/n/multilingual", ("o/n", {"subfolder": "multilingual"})),
        ("laya:o/n", ("o/n", {})),
    ])
    def test_load_forwards_the_subfolder_to_laya(self, spec, expected, monkeypatch):
        loads = self._fake_laya(monkeypatch)
        typed_scorer(spec)
        assert loads == [expected]


class TestTypedArmReachesTheGate:
    def test_typed_arm_scores_the_pairs_and_reports_its_cost(self, tmp_path, monkeypatch, capsys):
        """Guard for the wiring: the typed arm, not the judge, scores the gate's pairs."""
        import sys
        import types

        from dokimi.cli import build_parser
        from tests.test_judge_validity import ANSWER, OTHER_ANSWER

        agent = FakeAgent()
        module = types.ModuleType("laya")
        module.load = lambda repo, **kwargs: agent
        monkeypatch.setitem(sys.modules, "laya", module)
        answers = tmp_path / "answers.json"
        answers.write_text(json.dumps({"library_name": "lib", "answers": [
            {"question_id": f"q{i}", "question_text": "q?",
             "arms": {"baseline": {"answer": (ANSWER if i % 2 else OTHER_ANSWER) + f" {i}"}}}
            for i in range(6)]}))
        args = build_parser().parse_args(["eval", "judge", "validate", "--answers", str(answers),
                                          "--scorer", "typed:laya", "--bases", "6"])
        with pytest.raises(SystemExit):  # a constant scorer ties every pair: the gate FAILs
            args.func(args)
        assert len(agent.calls) > 0
        assert "arm cost:" in capsys.readouterr().out

    def test_saved_report_says_which_budget_revision_and_cost(self, tmp_path, monkeypatch):
        """A 512- and a 2048-token run of one checkpoint wrote the same label and no
        truncation count, so the file could not tell them apart."""
        import sys
        import types

        from dokimi.cli import build_parser
        from tests.test_judge_validity import ANSWER, OTHER_ANSWER

        agent = FakeAgent(usage={"truncated": True, "state_tokens_dropped": 40})
        agent.cfg, agent.revision = {"max_len": 512}, "55cf4c4"
        module = types.ModuleType("laya")
        module.load = lambda repo, **kwargs: agent
        monkeypatch.setitem(sys.modules, "laya", module)
        answers = tmp_path / "answers.json"
        answers.write_text(json.dumps({"library_name": "lib", "answers": [
            {"question_id": f"q{i}", "question_text": "q?",
             "arms": {"baseline": {"answer": (ANSWER if i % 2 else OTHER_ANSWER) + f" {i}"}}}
            for i in range(6)]}))
        labels = {}
        for max_len in ("0", "2048"):
            out = tmp_path / f"v{max_len}.json"
            args = build_parser().parse_args([
                "eval", "judge", "validate", "--answers", str(answers), "--scorer", "typed:laya",
                "--bases", "6", "--typed-max-len", max_len, "--output", str(out)])
            with pytest.raises(SystemExit):
                args.func(args)
            summary = json.loads(out.read_text(encoding="utf-8"))["summary"]
            arm = summary["typed_arm"]
            assert arm["scorer"] == "laya:convaiinnovations/laya" and arm["revision"] == "55cf4c4"
            assert arm["max_len"] == (512 if max_len == "0" else 2048)
            assert arm["usage"]["calls"] > 0 and arm["usage"]["truncated"] == arm["usage"]["calls"]
            labels[max_len] = summary["judge"]
        assert labels == {"0": "laya:convaiinnovations/laya max_len=512",
                          "2048": "laya:convaiinnovations/laya max_len=2048"}


class TestCliWiring:
    """`eval judge validate --scorer` must default to the judge and reject junk."""

    @staticmethod
    def _parse(*argv):
        from dokimi.cli import build_parser

        return build_parser().parse_args(["eval", "judge", "validate",
                                          "--answers", "a.json", *argv])

    def test_default_arm_is_the_shipped_judge(self):
        args = self._parse()
        assert args.scorer == "judge"
        assert args.typed_max_len == 0
        assert args.typed_threads is None

    def test_typed_arm_is_accepted_and_carries_its_knobs(self):
        args = self._parse("--scorer", "typed:laya:o/n/multilingual",
                           "--typed-max-len", "8192", "--typed-threads", "16")
        assert args.scorer == "typed:laya:o/n/multilingual"
        assert args.typed_max_len == 8192
        assert args.typed_threads == 16

    def test_unknown_typed_backend_exits_two(self, tmp_path, capsys):
        from dokimi.commands.evaluate import cmd_eval_judge_validity

        answers = tmp_path / "answers.json"
        answers.write_text(json.dumps({"library_name": "lib", "answers": []}))
        args = self._parse("--scorer", "typed:gpt2")
        args.answers = str(answers)
        with pytest.raises(SystemExit) as exc:
            cmd_eval_judge_validity(args)
        assert exc.value.code == 2
        assert "unknown typed scorer backend" in capsys.readouterr().err

    @pytest.mark.parametrize("spec", ["foo", "Judge", "laya"])
    def test_unknown_scorer_exits_two_not_a_traceback(self, spec, tmp_path, capsys):
        from dokimi.commands.evaluate import cmd_eval_judge_validity

        answers = tmp_path / "answers.json"
        answers.write_text(json.dumps({"library_name": "lib", "answers": []}))
        args = self._parse("--scorer", spec)
        args.answers = str(answers)
        with pytest.raises(SystemExit) as exc:
            cmd_eval_judge_validity(args)
        assert exc.value.code == 2
        assert "unknown --scorer" in capsys.readouterr().err


class TestSameScaleAsTheJudge:
    """The arm is only a baseline if it is on the judge's scale."""

    def test_aggregate_is_the_judges_own_computation(self):
        from dokimi.eval.judge import Judge

        scores = {"correctness": 73.0, "completeness": 41.0,
                  "code_quality": 88.0, "actionability": 12.0}
        assert typed_aggregate(scores) == Judge._compute_aggregate(
            scores, Judge._STANDARD_WEIGHTS)

    def test_a_judge_reweighting_moves_the_arm_with_it(self, monkeypatch):
        from dokimi.eval.judge import Judge

        scores = {"correctness": 100.0, "completeness": 0.0,
                  "code_quality": 0.0, "actionability": 0.0}
        before = typed_aggregate(scores)
        monkeypatch.setattr(Judge, "_STANDARD_WEIGHTS",
                            {**Judge._STANDARD_WEIGHTS, "correctness": 3.0})
        assert typed_aggregate(scores) != before

    def test_typed_dimensions_match_the_judges_weighted_ones(self):
        from dokimi.eval.judge import Judge

        assert set(TYPED_LEVELS) == set(Judge._STANDARD_WEIGHTS)

    def test_a_dimension_mismatch_is_caught(self, monkeypatch):
        import dokimi.eval.typed_scorer as ts

        monkeypatch.setattr(ts, "TYPED_LEVELS", {**ts.TYPED_LEVELS, "extra": ["a", "b"]})
        with pytest.raises(RuntimeError, match="does not match"):
            ts._check_dimensions()


class TestCliKnobs:
    """Bad knob values are rejected by argparse, not passed to the model."""

    @staticmethod
    def _parse(*argv):
        from dokimi.cli import build_parser

        return build_parser().parse_args(["eval", "judge", "validate",
                                          "--answers", "a.json", *argv])

    @pytest.mark.parametrize("argv", [["--typed-max-len", "-5"],
                                      ["--typed-threads", "0"],
                                      ["--typed-threads", "-2"],
                                      ["--typed-max-len", "x"]])
    def test_invalid_values_exit_two(self, argv):
        with pytest.raises(SystemExit) as exc:
            self._parse("--scorer", "typed:laya", *argv)
        assert exc.value.code == 2

    def test_zero_max_len_means_the_checkpoint_default(self):
        assert self._parse("--typed-max-len", "0").typed_max_len == 0

    def test_no_model_is_loaded_when_there_is_nothing_to_score(self, tmp_path,
                                                               monkeypatch, capsys):
        """An unusable answers file must fail before paying for a model load."""
        import dokimi.eval.typed_scorer as ts
        from dokimi.commands.evaluate import cmd_eval_judge_validity

        def boom(*a, **k):
            raise AssertionError("model loaded for an empty answers file")

        monkeypatch.setattr(ts, "typed_scorer", boom)
        answers = tmp_path / "answers.json"
        answers.write_text(json.dumps({"library_name": "lib", "answers": []}))
        args = self._parse("--scorer", "typed:laya")
        args.answers = str(answers)
        with pytest.raises(SystemExit) as exc:
            cmd_eval_judge_validity(args)
        assert exc.value.code == 2
        assert "no usable degradation pairs" in capsys.readouterr().err

    def test_an_all_no_op_run_names_the_real_cause(self, tmp_path, monkeypatch, capsys):
        """Valid answers whose every degradation is a no-op must not be reported
        as 'too few answers'."""
        import dokimi.eval.typed_scorer as ts
        from dokimi.commands.evaluate import cmd_eval_judge_validity

        monkeypatch.setattr(ts, "typed_scorer", lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("model loaded")))
        prose = ("This answer is long prose with no code at all. " * 40).strip()
        answers = tmp_path / "answers.json"
        answers.write_text(json.dumps({"library_name": "lib", "answers": [
            {"question_id": f"q{i}", "question_text": "q?",
             "arms": {"baseline": {"answer": prose + f" {i}"}}} for i in range(5)]}))
        args = self._parse("--scorer", "typed:laya", "--variants", "strip_code")
        args.answers = str(answers)
        with pytest.raises(SystemExit) as exc:
            cmd_eval_judge_validity(args)
        assert exc.value.code == 2
        err = capsys.readouterr().err
        assert "left the answer unchanged" in err and "strip_code" in err


class TestTypedArmWithTheReliabilityModes:
    """--mode pairwise, --sequential and the --concurrency pool all arrive with
    the scorer already built; none may change which arm is graded or costed."""

    @staticmethod
    def _args(tmp_path, *argv):
        from dokimi.cli import build_parser
        from tests.test_judge_validity import ANSWER, OTHER_ANSWER

        answers = tmp_path / "answers.json"
        answers.write_text(json.dumps({"library_name": "lib", "answers": [
            {"question_id": f"q{i}", "question_text": "q?",
             "arms": {"baseline": {"answer": (ANSWER if i % 2 else OTHER_ANSWER) + f" {i}"}}}
            for i in range(6)]}))
        return build_parser().parse_args(["eval", "judge", "validate", "--answers", str(answers),
                                          "--scorer", "typed:laya", *argv])

    def test_pairwise_mode_rejects_a_typed_scorer(self, tmp_path, monkeypatch, capsys):
        """The pairwise judge would grade the pairs and the report would call it the typed arm."""
        import dokimi.eval.typed_scorer as ts
        from dokimi.commands.evaluate import cmd_eval_judge_validity

        monkeypatch.setattr(ts, "typed_scorer", lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("model loaded")))
        with pytest.raises(SystemExit) as exc:
            cmd_eval_judge_validity(self._args(tmp_path, "--mode", "pairwise"))
        assert exc.value.code == 2
        assert "pairwise" in capsys.readouterr().err

    @pytest.mark.parametrize("argv,expected", [
        (["--sequential", "--max-bases", "7", "--bases", "3"], 7),
        (["--max-bases", "7", "--bases", "3"], 3),
    ], ids=["sequential", "plain"])
    def test_the_no_pairs_check_builds_max_bases_pairs_under_sequential(
            self, argv, expected, tmp_path, monkeypatch):
        """The run builds --max-bases pairs under --sequential, so the pre-check must too."""
        import dokimi.eval.degrade as degrade
        from dokimi.commands.evaluate import cmd_eval_judge_validity

        asked = []
        monkeypatch.setattr(degrade, "build_pairs",
                            lambda *a, **k: asked.append(k["n_bases"]) or [])
        with pytest.raises(SystemExit):
            cmd_eval_judge_validity(self._args(tmp_path, *argv))
        assert asked == [expected]

    @pytest.mark.parametrize("extra", [[], ["--sequential", "--max-bases", "6"]],
                             ids=["plain", "sequential"])
    def test_a_typed_arm_prints_its_cost_and_is_not_run_on_a_pool(
            self, extra, tmp_path, monkeypatch, capsys):
        """One model, and usage seconds that are summed: on a pool of 5 they would
        report about five times the wall time."""
        import sys
        import time
        import types

        class Slow(FakeAgent):
            def predict(self, state, questions, **kwargs):
                time.sleep(0.05)
                return super().predict(state, questions, **kwargs)

        agent = Slow()
        module = types.ModuleType("laya")
        module.load = lambda repo, **kwargs: agent
        monkeypatch.setitem(sys.modules, "laya", module)
        out = tmp_path / "r.json"
        args = self._args(tmp_path, "--bases", "6", "--variants", "strip_code",
                          "--concurrency", "5", "--output", str(out), *extra)
        started = time.time()
        with pytest.raises(SystemExit):  # a constant scorer ties every pair: the gate FAILs
            args.func(args)
        wall = time.time() - started
        assert "arm cost:" in capsys.readouterr().out
        usage = json.loads(out.read_text(encoding="utf-8"))["summary"]["typed_arm"]["usage"]
        assert usage["calls"] >= 4
        assert usage["seconds"] <= wall


class TestTheArmFailsLoudly:
    """A usage error is exit 2 with a message; a quiet wrong result is worse."""

    @staticmethod
    def _run(tmp_path, *argv):
        args = TestTypedArmWithTheReliabilityModes._args(
            tmp_path, "--bases", "6", "--variants", "strip_code", *argv)
        with pytest.raises(SystemExit) as exc:
            args.func(args)
        return exc.value.code

    def test_an_unloadable_checkpoint_exits_two_naming_the_spec(self, tmp_path, monkeypatch, capsys):
        """laya.load raises OSError for a typo, a missing subfolder or an offline Hub."""
        import sys
        import types

        def load(repo, **kwargs):
            raise OSError("repo not found")

        module = types.ModuleType("laya")
        module.load = load
        monkeypatch.setitem(sys.modules, "laya", module)
        assert self._run(tmp_path, "--scorer", "typed:laya:no/such") == 2
        err = capsys.readouterr().err
        assert "cannot load typed scorer laya:no/such" in err and "repo not found" in err

    def test_a_missing_extra_exits_two_naming_it(self, tmp_path, monkeypatch, capsys):
        import builtins
        import sys

        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "laya":
                raise ImportError("No module named 'laya'")
            return real_import(name, *args, **kwargs)

        monkeypatch.delitem(sys.modules, "laya", raising=False)
        monkeypatch.setattr(builtins, "__import__", fake_import)
        assert self._run(tmp_path) == 2
        assert "dokimi[typed-scorer]" in capsys.readouterr().err

    def test_typed_threads_reach_torch(self, tmp_path, monkeypatch):
        import sys
        import types

        seen = []
        torch = types.ModuleType("torch")
        torch.set_num_threads = seen.append
        laya = types.ModuleType("laya")
        laya.load = lambda repo, **kwargs: FakeAgent()
        monkeypatch.setitem(sys.modules, "torch", torch)
        monkeypatch.setitem(sys.modules, "laya", laya)
        self._run(tmp_path, "--typed-threads", "3")  # a constant scorer: the gate FAILs
        assert seen == [3]

    def test_scorer_names_that_only_start_with_typed_are_unknown(self, tmp_path, capsys):
        """'typedfoo' must not be read as the bare 'typed' arm."""
        assert self._run(tmp_path, "--scorer", "typedfoo") == 2
        assert "unknown --scorer" in capsys.readouterr().err

    def test_criteria_are_sent_in_the_order_the_levels_are_scored(self):
        """The backend returns a slot index; reversing the options would invert every score."""
        questions = TypedScorer("laya", agent=FakeAgent())._questions("oneTBB")
        assert list(questions) == list(TYPED_LEVELS)
        assert all(questions[d]["criteria"] == list(levels) for d, levels in TYPED_LEVELS.items())

    def test_a_judged_dimension_the_arm_never_asks_about_is_caught(self, monkeypatch):
        """It would score as 0 and drag every typed aggregate down without an error."""
        import dokimi.eval.typed_scorer as ts

        monkeypatch.setattr(ts, "TYPED_LEVELS", {k: v for k, v in TYPED_LEVELS.items()
                                                 if k != "code_quality"})
        with pytest.raises(RuntimeError, match="does not match"):
            ts._check_dimensions()
