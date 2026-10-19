# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""No new test that cannot fail for a wrong behaviour, and the auditor that finds them.

``scripts/audit_test_quality.py`` flags tests whose asserts only check a type or an
existence, echo the test's own input, pin a constant's spelling, only check that a mock
was called, assert nothing, or repeat another test's body. ``BASELINE`` freezes the
findings that may stay; it is empty and only shrinks.
"""

from __future__ import annotations

import importlib.util
import json
import textwrap
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "audit_test_quality.py"
_spec = importlib.util.spec_from_file_location("audit_test_quality", _SCRIPT)
assert _spec and _spec.loader
atq = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(atq)

#: ``flag -> {"tests/test_x.py::Class.test_y"}`` allowed to stay flagged. Never add to it:
#: make the test check behaviour, delete it, or mark it ``# test-quality: ok <reason>`` when
#: the shape is deliberate. When a finding is fixed, delete it here in the same change.
BASELINE: dict[str, frozenset[str]] = {}


@pytest.fixture(scope="module")
def findings() -> dict[str, set[str]]:
    flagged, _ = atq.audit_tree()
    return atq.gated_findings(flagged)


def test_no_new_test_that_cannot_fail(findings):
    new = [e for e in atq.ratchet_errors(findings, BASELINE) if ": new: " in e]
    assert not new, (
        "\n".join(new) + "\nMake each test check the behaviour it exists for (a value, a call "
        "argument, an output), delete it if another test covers that, or, when the shape is "
        f"deliberate (a must-not-raise smoke test), add `# {atq.EXEMPT_MARKER} <reason>` to its "
        "def line. `python scripts/audit_test_quality.py` lists every finding."
    )


def test_a_fixed_finding_leaves_the_baseline_too(findings):
    # Otherwise the finding could come back without failing the test above.
    stale = [e for e in atq.ratchet_errors(findings, BASELINE) if ": new: " not in e]
    assert not stale, "\n".join(stale) + "\ndelete these keys from BASELINE in this test"


def test_the_baseline_names_only_gated_flags():
    assert set(BASELINE) <= set(atq.GATED_FLAGS)


# ── the auditor ──────────────────────────────────────────────────────────────


def _flags(source: str) -> dict[str, tuple[str, ...]]:
    """``test name -> flags`` for every test in *source*."""
    return {a.test: a.flags for a in atq.audit_source(textwrap.dedent(source))}


FLAGGED = {
    "type_only": """
        def test_x():
            out = build()
            assert out is not None
            assert isinstance(out, dict)
            assert len(out) > 0
            assert hasattr(out, "keys") and callable(out.keys)
        """,
    "mock_echo": """
        from unittest.mock import patch
        def test_x():
            with patch("mod.send") as send:
                run()
            send.assert_called_once()
            assert send.call_count == 1
        """,
    "set_then_read": """
        def test_x():
            cfg = Config(model="m", retries=3)
            assert cfg.model == "m"
            assert cfg.retries == 3
        """,
    "constant_mirror": """
        from dokimi.artifacts import SCHEMA_VERSIONS, Kind
        def test_x():
            assert SCHEMA_VERSIONS == "answers.v1"
            assert Kind.ANSWERS.value == "answers"
        """,
    "no_assert": """
        def test_x():
            run(config={"a": 1})
        """,
}


@pytest.mark.parametrize("flag", sorted(FLAGGED))
def test_each_flag_is_raised_by_its_shape(flag):
    assert flag in _flags(FLAGGED[flag])["test_x"]


@pytest.mark.parametrize("flag", sorted(FLAGGED))
def test_each_gated_flag_fails_the_ratchet(flag):
    found = atq.gated_findings(atq.audit_source(textwrap.dedent(FLAGGED[flag]), "t.py"))
    assert f"{flag}: new: t.py::test_x" in atq.ratchet_errors(found, {})


def test_a_short_weak_test_is_reported_as_trivial_but_not_gated():
    assert _flags(FLAGGED["set_then_read"])["test_x"] == ("set_then_read", "trivial_size")
    assert "trivial_size" not in atq.GATED_FLAGS


def test_a_body_repeated_under_another_name_is_a_duplicate():
    flags = _flags("""
        def test_a(tmp_path):
            assert run(tmp_path) == 1

        def test_b(tmp_path):
            assert run(tmp_path) == 1
        """)
    assert flags == {"test_a": (), "test_b": ("duplicate",)}


@pytest.mark.parametrize("source", [
    # different fixtures, decorators or enclosing classes give the same body another meaning
    """
    def test_a(tmp_path):
        assert run() == 1
    def test_b(monkeypatch):
        assert run() == 1
    """,
    """
    import pytest
    @pytest.mark.parametrize("x", [1])
    def test_a(x):
        assert run(x) == 1
    @pytest.mark.parametrize("x", [2])
    def test_b(x):
        assert run(x) == 1
    """,
    """
    class TestOne:
        def test_a(self):
            assert run() == 1
    class TestTwo:
        def test_a(self):
            assert run() == 1
    """,
])
def test_a_body_with_other_inputs_is_not_a_duplicate(source):
    assert "duplicate" not in {f for flags in _flags(source).values() for f in flags}


NOT_FLAGGED = {
    "is_none": "assert find() is None",
    "truthiness": "assert not offenders",
    "negated_hasattr": "assert not hasattr(obj, 'legacy')",
    "project_class": "assert isinstance(make('openai'), OpenAIJudge)",
    "exact_length": "assert len(make()) == 3",
    "value": "assert make() == {'a': 1}",
    "raises": "with pytest.raises(ValueError):\n        make('bad')",
    "called_with": "with patch('m.send') as send:\n        run()\n    send.assert_called_with('x')",
    "call_args": "with patch('m.send') as send:\n        run()\n    assert send.call_args.args == ('x',)",
    "count_and_value": "with patch('m.send') as send:\n        out = run()\n"
                       "    assert send.call_count == 2 and out == 70.0",
    "transformed_echo": "cfg = load(Config(model='m'))\n    assert cfg.model == 'm'",
    "rebound_echo": "cfg = Config(model='m')\n    cfg = cfg.merged()\n    assert cfg.model == 'm'",
    "foreign_constant": "from os import SEP\n    assert SEP == '/'",
    "asserting_helper": "_check_output(run())",
    "hinted_call": "verify_layout(run())",
}


@pytest.mark.parametrize("name", sorted(NOT_FLAGGED))
def test_a_check_of_behaviour_is_not_flagged(name):
    source = (
        "def _check_output(out):\n    assert out == 1\n\n\n"
        f"def test_x():\n    {NOT_FLAGGED[name]}\n"
    )
    assert _flags(source)["test_x"] == ()


def test_a_helper_counts_when_it_asserts_through_another_helper():
    flags = _flags("""
        def _inner(x):
            assert x == 1
        def _outer(x):
            _inner(x)
        def _quiet(x):
            print(x)
        def test_via_helpers():
            _outer(run())
        def test_via_a_quiet_helper():
            _quiet(run())
        """)
    assert flags == {"test_via_helpers": (), "test_via_a_quiet_helper": ("no_assert",)}


def test_the_exemption_marker_needs_a_reason():
    audits = {a.test: a for a in atq.audit_source(textwrap.dedent("""
        import pytest
        def test_smoke():  # test-quality: ok must not raise on an empty config
            run({})
        @pytest.mark.slow  # test-quality: ok must not raise
        def test_decorated():
            run({})
        def test_bare():  # test-quality: ok
            run({})
        """), "t.py")}
    assert {name: a.exempt for name, a in audits.items()} == {
        "test_smoke": True, "test_decorated": True, "test_bare": False}
    assert atq.gated_findings(audits.values())["no_assert"] == {"t.py::test_bare"}


def test_methods_are_keyed_by_class_and_lines_do_not_enter_the_key():
    (audit,) = atq.audit_source("class TestA:\n    def test_x(self):\n        run()\n", "t.py")
    assert (audit.key, audit.line) == ("t.py::TestA.test_x", 2)


def test_a_fixed_finding_is_reported_as_stale():
    found = {flag: set() for flag in atq.GATED_FLAGS}
    assert atq.ratchet_errors(found, {"no_assert": frozenset({"t.py::test_x"})}) == [
        "no_assert: fixed, remove from the baseline: t.py::test_x"]


def test_the_report_and_json_list_every_flagged_test(tmp_path, capsys):
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_mod.py").write_text(textwrap.dedent(FLAGGED["no_assert"]), encoding="utf-8")
    out_json = tmp_path / "tq.json"
    assert atq.main(["--tests-dir", str(tests), "--json", str(out_json)]) == 0
    assert "test_mod.py::test_x  no_assert" in capsys.readouterr().out
    rows = json.loads(out_json.read_text(encoding="utf-8"))
    assert [(Path(r["file"]).name, r["test"], r["flags"], r["exempt"]) for r in rows] == [
        ("test_mod.py", "test_x", ["no_assert"], False)]


def test_constructor_tracking_invalidates_after_mutation():
    """A mutation after construction invalidates the echo check."""
    flags = _flags("""
        def test_mutation_invalidates_echo():
            cfg = Config(retries=3)
            cfg.retries = 9
            cfg.reset()
            assert cfg.retries == 3
        """)
    assert "set_then_read" not in flags["test_mutation_invalidates_echo"]


def test_tuple_comparison_is_not_mock_only():
    """A tuple comparison with both mock and value checks is not mock-only."""
    flags = _flags("""
        from unittest.mock import patch
        def test_tuple_and_value():
            with patch("mod.send") as send:
                out = run()
            assert (send.call_count, out) == (1, 42)
        """)
    assert "mock_echo" not in flags["test_tuple_and_value"]


def test_weak_assert_with_asserting_helper_is_not_type_only():
    """A weak direct assert plus an asserting helper counts as behavioral."""
    flags = _flags("""
        def _check_output(x):
            assert x == 42
        def test_weak_plus_helper():
            out = run()
            assert isinstance(out, dict)
            _check_output(out.get("value"))
        """)
    assert "type_only" not in flags["test_weak_plus_helper"]


def test_different_defaults_do_not_duplicate():
    """Tests with different parameter defaults are not duplicates."""
    flags = _flags("""
        def test_a(x=1):
            assert transform(x) == x * 10
        def test_b(x=2):
            assert transform(x) == x * 10
        """)
    assert flags == {"test_a": (), "test_b": ()}
