# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""``benchmark run --registry`` resolves the library id through that registry."""

import pytest

from dokimi import orchestrator
from dokimi.commands import benchmark
from dokimi.orchestrator.pipeline import EvaluationPipeline
from tests.test_answering_stack_parity import _subparser

CUSTOM = """\
products:
  my_lib:
    name: My Lib
    description: A library only this registry knows
    doc_sources: [context7]
  onetbb:
    name: Custom TBB
    description: A key that the bundled registry also has, with no id here
    doc_sources: [context7]
"""


class _DeclaredIdClient:
    """A doc source like Context7: a name that is not a declared id is not resolved."""

    requires_declared_library_id = True

    def resolve_library_id(self, name):
        return name


class _Spy:
    """Stands in for EvaluationPipeline: keeps what ``benchmark run`` hands it."""

    built: list = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        _Spy.built.append(kwargs)

    def run(self, concurrency):
        return {"steps": {}}


@pytest.fixture
def spy(monkeypatch):
    _Spy.built = []
    monkeypatch.setattr(orchestrator, "EvaluationPipeline", _Spy)
    return _Spy


def _run(tmp_path, library, *extra):
    registry = tmp_path / "my.yaml"
    registry.write_text(CUSTOM, encoding="utf-8")
    args = _subparser("benchmark", "run").parse_args(
        ["--library", library, "--registry", str(registry), "--output-dir", str(tmp_path / "out"), *extra]
    )
    benchmark.cmd_benchmark_run(args)


@pytest.mark.parametrize("library", ["my_lib", "onetbb"])
def test_a_custom_entry_without_an_id_is_not_looked_up_in_the_bundled_registry(spy, tmp_path, library):
    _run(tmp_path, library)

    (kwargs,) = spy.built
    real = EvaluationPipeline(**kwargs)
    with pytest.raises(ValueError, match="declares no 'context7_id'"):
        real._library_id(_DeclaredIdClient())


def test_multi_run_and_batch_hand_the_registry_over_too(spy, tmp_path):
    _run(tmp_path, "my_lib", "--multi-run", "2")
    registry = tmp_path / "my.yaml"
    args = _subparser("benchmark", "batch").parse_args(
        ["--libraries", "my_lib", "--registry", str(registry), "--output-dir", str(tmp_path / "batch")]
    )
    benchmark.cmd_benchmark_batch(args)

    assert len(spy.built) == 3
    for kwargs in spy.built:
        assert "my_lib" in kwargs["registry"]


class _SourceIdClient(_DeclaredIdClient):
    """An ``mcp:`` source with ``;id=``: the id it was given is its default."""

    default_library_id = "my/override"


def test_an_mcp_source_id_wins_over_the_entry_id(spy, tmp_path):
    registry = tmp_path / "my.yaml"
    registry.write_text(CUSTOM.replace(
        "doc_sources: [context7]\n  onetbb", "doc_sources: [context7]\n    context7_id: org/declared\n  onetbb", 1,
    ), encoding="utf-8")
    args = _subparser("benchmark", "run").parse_args(
        ["--library", "my_lib", "--registry", str(registry), "--output-dir", str(tmp_path / "out")]
    )
    benchmark.cmd_benchmark_run(args)

    (kwargs,) = spy.built
    real = EvaluationPipeline(**kwargs)
    assert real._library_id(_DeclaredIdClient()) == "org/declared"
    assert real._library_id(_SourceIdClient()) == "my/override"


@pytest.mark.parametrize("command", [
    ["preflight", "--library", "my_lib"], ["run", "--library", "my_lib"], ["batch", "--all"],
])
@pytest.mark.parametrize("text,message", [
    ("products: [unclosed\n", "cannot read --registry {path}: "),
    ("products: 2026-99-99\n", "cannot read --registry {path}: "),
    (None, "--registry {path}: not a file"),
    ("", "--registry {path}: not a file"),
], ids=["syntax", "bad-date", "missing", "directory"])
def test_a_bad_registry_file_is_an_error_line_not_a_traceback(spy, tmp_path, monkeypatch, capsys, command, text,
                                                              message):
    from dokimi import cli

    registry = tmp_path / "my.yaml"
    if text == "":
        registry.mkdir()
    elif text is not None:
        registry.write_text(text, encoding="utf-8")
    monkeypatch.setattr("sys.argv", ["dokimi", "benchmark", *command, "--registry", str(registry),
                                     "--output-dir", str(tmp_path / "out")])
    with pytest.raises(SystemExit) as exc_info:
        cli.main()

    err = capsys.readouterr().err
    assert exc_info.value.code == 2
    assert err.startswith("Error: " + message.format(path=registry)) and "Traceback" not in err
    assert spy.built == []
