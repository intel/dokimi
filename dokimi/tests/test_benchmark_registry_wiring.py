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
