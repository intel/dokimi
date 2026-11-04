# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""personas discover and questions analyze: an unwritable output path is a clean exit, not a traceback."""

from types import SimpleNamespace

import pytest

from dokimi import cli
from dokimi.commands import personas as personas_cmd


class _FakeGenerator:
    def __init__(self, **_):
        pass

    def generate_personas(self, **_):
        return {"personas": [{"id": "p1"}]}

    def save_personas(self, personas, output_path):
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text("{}", encoding="utf-8")


class _FakeAnalyzer:
    def __init__(self, **_):
        pass

    @staticmethod
    def create_minimal_analysis(**_):
        return {}

    def analyze_repository(self, _repo):
        return {}

    def save_analysis(self, analysis, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}", encoding="utf-8")


def _outputs(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x", encoding="utf-8")
    directory = tmp_path / "dir"
    directory.mkdir()
    return {"under-a-file": blocker / "out.json", "is-a-directory": directory}


@pytest.fixture
def fakes(monkeypatch):
    monkeypatch.setattr(personas_cmd, "PersonaGenerator", _FakeGenerator)
    monkeypatch.setattr(personas_cmd, "PersonaAnalyzer", _FakeAnalyzer)


@pytest.mark.parametrize("kind", ["under-a-file", "is-a-directory"])
def test_discover_exits_2_when_the_personas_output_cannot_be_written(tmp_path, capsys, fakes, kind):
    out = _outputs(tmp_path)[kind]
    args = SimpleNamespace(repo=None, description="d", product="p", output=str(out), count=1,
                           model="m", provider="x", save_analysis=False, github_token=None)
    with pytest.raises(SystemExit) as exc:
        personas_cmd.cmd_personas_discover(args)
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert err.startswith("Error: cannot write ") and str(out) in err


def test_discover_exits_2_when_the_analysis_cannot_be_written(tmp_path, capsys, fakes):
    blocker = tmp_path / "file"
    blocker.write_text("x", encoding="utf-8")
    args = SimpleNamespace(repo="o/r", description=None, product="p", output=str(blocker / "out.json"),
                           count=1, model="m", provider="x", save_analysis=True, github_token="t")
    with pytest.raises(SystemExit) as exc:
        personas_cmd.cmd_personas_discover(args)
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert err.startswith("Error: cannot write ") and "p_analysis.json" in err


@pytest.mark.parametrize("kind", ["under-a-file", "is-a-directory"])
def test_analyze_exits_2_when_the_report_cannot_be_written(tmp_path, monkeypatch, capsys, kind):
    from dokimi.questions import quality_analyzer

    class _Report:
        difficulty_distribution = {}
        total = 0
        trivial_count = 0
        trivial_pct = 0
        diversity_score = 0.0
        recommendations = []
        trivial_questions = []

    class _Analyzer:
        def __init__(self, **_):
            pass

        def analyze(self, *_a, **_k):
            return _Report()

        def save_report(self, report, path):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("{}", encoding="utf-8")

    monkeypatch.setattr(quality_analyzer, "QuestionQualityAnalyzer", _Analyzer)
    qfile = tmp_path / "q.json"
    qfile.write_text('["a?"]', encoding="utf-8")
    out = _outputs(tmp_path)[kind]
    monkeypatch.setattr("sys.argv", ["dokimi", "eval", "questions", "analyze", "--questions", str(qfile),
                                     "--product", "p", "--output", str(out)])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert err.startswith("Error: cannot write ") and str(out) in err
