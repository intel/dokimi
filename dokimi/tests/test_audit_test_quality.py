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
    flagged, total = atq.audit_tree()
    # A scan that finds nothing would pass every check below.
    assert total > 0, f"no test functions found under {atq.TESTS}"
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
    # every part of the signature counts: keyword-only and positional-only fixtures,
    # keyword-only defaults, *args and **kwargs
    """
    def test_a(*, tmp_path):
        assert run() == 1
    def test_b(*, monkeypatch):
        assert run() == 1
    """,
    """
    def test_a(tmp_path, /):
        assert run() == 1
    def test_b(monkeypatch, /):
        assert run() == 1
    """,
    """
    def test_a(*, x=1):
        assert transform(x) == x * 10
    def test_b(*, x=2):
        assert transform(x) == x * 10
    """,
    """
    def test_a(*args):
        assert run() == 1
    def test_b():
        assert run() == 1
    """,
    """
    def test_a(**kwargs):
        assert run() == 1
    def test_b():
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


def test_the_exemption_marker_in_a_string_exempts_nothing():
    (audit,) = atq.audit_source(textwrap.dedent("""
        import pytest
        @pytest.mark.parametrize("text", ["test-quality: ok example"])
        def test_x(text):
            run(text)
        """))
    assert (audit.flags, audit.exempt) == (("no_assert",), False)


@pytest.mark.parametrize("marker", ["# test-quality: okay", "# test-quality: ok.", "# test-quality: ok,why"])
def test_the_marker_word_must_be_followed_by_whitespace_and_a_reason(marker):
    (audit,) = atq.audit_source(textwrap.dedent(f"""
        def test_x():  {marker}
            run()
        """), "t.py")
    assert audit.exempt is False


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


@pytest.mark.parametrize("where", [".", "unit/llm"])
def test_every_file_pytest_collects_is_audited(tmp_path, where):
    folder = tmp_path / where
    folder.mkdir(parents=True, exist_ok=True)
    for name in ("test_a.py", "b_test.py"):
        (folder / name).write_text(textwrap.dedent(FLAGGED["no_assert"]), encoding="utf-8")
    flagged, total = atq.audit_tree(tmp_path)
    assert (sorted(Path(a.file).name for a in flagged), total) == (["b_test.py", "test_a.py"], 2)


def test_the_report_marks_only_an_exempt_test_as_exempt():
    flagged = [atq.Audit("tests/test_m.py", 3, "test_x", ("no_assert",), exempt=True),
               atq.Audit("tests/test_m.py", 7, "test_y", ("no_assert",))]
    assert atq.report(flagged, 2).splitlines()[-2:] == [
        "tests/test_m.py::test_x  no_assert  (exempt)", "tests/test_m.py::test_y  no_assert"]


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


def test_a_patched_test_that_expects_a_raise_is_not_a_mock_echo():
    """The raise is the outcome; the called check next to it does not make it an echo."""
    flags = _flags("""
        def test_x():
            with patch("m.y") as y:
                with pytest.raises(ValueError):
                    run()
            assert y.called
        """)
    assert "mock_echo" not in flags["test_x"]


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


def test_a_helper_that_only_checks_a_shape_does_not_hide_type_only():
    flags = _flags("""
        def _shape(out):
            assert isinstance(out, dict)
        def _value(out):
            assert out == {"a": 1}
        def test_inline_shape():
            out = run()
            assert out is not None
            assert isinstance(out, dict)
        def test_shape_through_a_helper():
            out = run()
            assert out is not None
            _shape(out)
        def test_value_through_a_helper():
            out = run()
            assert out is not None
            _value(out)
        """)
    assert "type_only" in flags["test_inline_shape"]
    assert "type_only" in flags["test_shape_through_a_helper"]
    assert flags["test_value_through_a_helper"] == ()


@pytest.mark.parametrize("patching", [
    "@patch('mod.send')\ndef test_x(send):\n    run()\n    send.assert_called_once()",
    "def test_x():\n    with patch.object(mod, 'send') as send:\n        run()\n    send.assert_called_once()",
    "def test_x():\n    with patch.dict(os.environ, {}) as send:\n        run()\n    send.assert_called_once()",
    "def test_x(monkeypatch):\n    send = Mock()\n    monkeypatch.setattr(mod, 'send', send)\n"
    "    run()\n    send.assert_called_once()",
])
def test_every_spelling_of_patching_makes_a_call_check_a_mock_echo(patching):
    assert "mock_echo" in _flags(patching)["test_x"]


def test_a_mock_call_check_without_patching_is_not_a_mock_echo():
    assert "mock_echo" not in _flags("""
        def test_x():
            send = make_spy()
            run(send)
            send.assert_called_once()
        """)["test_x"]


@pytest.mark.parametrize("check", ["len(out) > 0", "0 < len(out)", "len(out) >= 1", "len(out) != 0"])
def test_a_length_check_alone_is_type_only(check):
    flags = _flags(f"def test_x():\n    out = run()\n    assert {check}\n")
    assert "type_only" in flags["test_x"]


@pytest.mark.parametrize("mutation", [
    "c.n += 1",  # AugAssign
    "c.n: int = 5",  # AnnAssign with value
    "c.n, y = 5, 6",  # Tuple targets
    "setattr(c, 'n', 7)",  # setattr() call
    "r = c.bump()",  # method call assigned, not an expression statement
    "c.items.append(3)",  # method call on an attribute of the object
    "(c := Config(n=9))",  # walrus rebind
    "mutate(c)",  # object passed as an argument
    "mutate(target=c)",  # object passed as a keyword value
    "mutate([c])",  # object inside a list argument
    "mutate({'k': c})",  # object inside a dict argument
    "mutate(**{'k': c})",  # object inside a dict unpacked as keywords
    "c.d['a'] = 3",  # subscript store on the object
    "for c in items(): pass",  # for-loop rebind
    "with open_c() as c: pass",  # with-statement rebind
])
def test_mutation_patterns_invalidate_echo(mutation):
    """All mutation patterns invalidate the constructor echo check."""
    flags = _flags(f"""
        def test_mutation():
            c = Config(n=3)
            {mutation}
            assert c.n == 3
        """)
    assert "set_then_read" not in flags["test_mutation"]


@pytest.mark.parametrize("weak", [
    "assert hasattr(c, 'n')", "assert isinstance(c, object)", "assert callable(c)", "print(c)",
])
def test_a_read_only_builtin_does_not_count_as_a_mutation(weak):
    flags = _flags(f"""
        def test_x():
            c = Config(n=3)
            {weak}
            assert c.n == 3
        """)
    assert "set_then_read" in flags["test_x"]


def test_and_or_splits_in_mock_echo():
    """Both and/or expressions are judged element-wise in mock_echo."""
    # Both are mock-only when split
    flags = _flags("""
        from unittest.mock import patch
        def test_and_split():
            with patch("mod.send") as send:
                run()
            assert send.call_count == 1 and send.called
        """)
    assert "mock_echo" in flags["test_and_split"]

    flags = _flags("""
        from unittest.mock import patch
        def test_or_split():
            with patch("mod.send") as send:
                run()
            assert send.call_count == 1 or send.called
        """)
    assert "mock_echo" in flags["test_or_split"]


@pytest.mark.parametrize("check,flag", [
    ("out == 42 or send.called", "mock_echo"),
    ("out == 42 or out is not None", "type_only"),
])
def test_an_or_with_one_check_that_cannot_fail_is_flagged(check, flag):
    flags = _flags(f"""
        from unittest.mock import patch
        def test_x():
            with patch("mod.send") as send:
                out = run()
            assert {check}
        """)
    assert flag in flags["test_x"]


@pytest.mark.parametrize("call_check", ["send.assert_called_once()", "assert send.called"])
def test_a_call_only_mock_check_does_not_hide_type_only(call_check):
    flags = _flags(f"""
        from unittest.mock import patch
        def test_x():
            with patch("mod.send") as send:
                out = run()
            assert isinstance(out, str)
            {call_check}
        """)
    assert "type_only" in flags["test_x"]


def test_a_skip_check_next_to_a_weak_assert_is_not_type_only():
    flags = _flags("""
        def test_x(llm):
            out = run(llm, gaps=0)
            assert isinstance(out, list)
            llm.assert_not_called()
        """)
    assert flags["test_x"] == ()


@pytest.mark.parametrize("source", [
    # A skip test: the code under test chose not to call the patched dependency.
    "@patch('m.llm')\ndef test_x(llm):\n    run(skip=True)\n    llm.assert_not_called()",
    "@patch('m.llm', new_callable=AsyncMock)\ndef test_x(llm):\n    run(skip=True)\n"
    "    llm.assert_not_awaited()",
    "@patch('m.llm')\ndef test_x(llm):\n    run(skip=True)\n    assert not llm.called",
    "@patch('m.llm')\ndef test_x(llm):\n    run(skip=True)\n    assert llm.call_count == 0",
    "@patch('m.llm')\n@patch('m.cache')\ndef test_x(cache, llm):\n    run()\n"
    "    cache.assert_called_once()\n    llm.assert_not_called()",
    # call_count on what the code under test returned is data, not the patch.
    "@patch('m.clock')\ndef test_x(clock):\n    stats = run()\n    assert stats.call_count == 3",
    "@patch('m.clock', new=fake_clock)\ndef test_x(stats):\n    run()\n    assert stats.call_count == 3",
    "def test_x():\n    with patch('m.clock'):\n        stats = run()\n    assert stats.called",
    "@patch('m.llm')\ndef test_x(llm):\n    run(skip=True)\n    assert 0 == llm.call_count",
])
def test_a_not_called_check_or_a_count_on_data_is_not_a_mock_echo(source):
    assert "mock_echo" not in _flags(source)["test_x"]


@pytest.mark.parametrize("source", [
    # The near-miss of the skip test: the patched mock was called, and nothing else is checked.
    "@patch('m.llm')\ndef test_x(llm):\n    run()\n    assert llm.called",
    "@patch('m.llm')\ndef test_x(llm):\n    run()\n    assert llm.call_count == 1",
    "@patch('m.llm')\n@patch('m.cache')\ndef test_x(cache, llm, tmp_path):\n    run()\n"
    "    assert llm.call_count == 1",
    "def test_x():\n    with patch('m.llm') as llm:\n        run()\n    chat = llm.return_value.chat\n"
    "    assert chat.call_count == 1",
    "def test_x():\n    p = patch('m.llm')\n    llm = p.start()\n    run()\n    assert llm.called",
    "def test_x(mocker):\n    llm = mocker.patch('m.llm')\n    run()\n    assert llm.await_count == 1",
    "@patch('m.x', new=1)\ndef test_x(mock_llm):\n    run()\n    assert mock_llm.call_count == 1",
    # the replacement handed to a patch is a mock, whatever it is called
    "def test_x(monkeypatch, fake):\n    monkeypatch.setattr(mod, 'f', fake)\n    run()\n    assert fake.called",
    "async def test_x():\n    async with patch('m.llm') as llm:\n        await run()\n    assert llm.called",
    # the first parameter of a method is self, not the injected mock
    "class TestX:\n    @patch('m.llm')\n    def test_x(self, llm):\n        run()\n        assert llm.called",
])
def test_a_called_check_on_a_patched_mock_is_still_a_mock_echo(source):
    audits = {a.test.rsplit(".", 1)[-1]: a.flags for a in atq.audit_source(textwrap.dedent(source))}
    assert "mock_echo" in audits["test_x"]


@pytest.mark.parametrize("source", [
    # a mock held on self by setUp
    "class TestX:\n    def setUp(self):\n        self.m = MagicMock()\n    def test_x(self):\n"
    "        with patch('m.y'):\n            run(self.m)\n        assert self.m.called",
    # a mock built by a fixture of the file, under another name than mock_*
    "@pytest.fixture\ndef llm():\n    return MagicMock()\ndef test_x(llm):\n    with patch('m.y'):\n"
    "        run(llm)\n    assert llm.called",
    # the call form of the decorator
    "@pytest.fixture(scope='module')\ndef llm():\n    return MagicMock()\ndef test_x(llm):\n"
    "    with patch('m.y'):\n        run(llm)\n    assert llm.called",
    # the fixture arrives as a keyword-only or a positional-only parameter
    "@pytest.fixture\ndef llm():\n    return MagicMock()\ndef test_x(*, llm):\n    with patch('m.y'):\n"
    "        run(llm)\n    assert llm.called",
    "@pytest.fixture\ndef llm():\n    return MagicMock()\ndef test_x(llm, /):\n    with patch('m.y'):\n"
    "        run(llm)\n    assert llm.called",
])
def test_a_mock_bound_outside_the_test_body_is_still_a_mock_echo(source):
    audits = {a.test.rsplit(".", 1)[-1]: a.flags for a in atq.audit_source(textwrap.dedent(source))}
    assert "mock_echo" in audits["test_x"]


def test_a_plain_helper_named_like_the_parameter_is_not_a_mock():
    flags = _flags("""
        def llm():
            return MagicMock()
        @pytest.fixture
        def other():
            return MagicMock()
        def test_x(llm):
            with patch("m.y"):
                run()
            assert llm.call_count == 2
        """)
    assert "mock_echo" not in flags["test_x"]


def test_a_fixture_that_returns_data_is_not_a_mock():
    flags = _flags("""
        @pytest.fixture
        def stats():
            return run()
        def test_x(stats):
            with patch("m.y"):
                run()
            assert stats.call_count == 2
        """)
    assert "mock_echo" not in flags["test_x"]


def test_tuple_of_mock_attrs_and_literals_is_mock_echo():
    """A tuple of mock attributes against literal expectations only reads the mock."""
    flags = _flags("""
        from unittest.mock import patch
        def test_tuple_mock_only():
            with patch("mod.send") as send:
                run()
            assert (send.call_count, send.called) == (1, True)
        """)
    assert "mock_echo" in flags["test_tuple_mock_only"]


@pytest.mark.parametrize("check,expected_no_assert", [
    ("verify_result(run())", False),  # Bare hinted call: not no_assert
    ("v.verify_result(run())", True),  # Method call: still no_assert
    ("_check_output(run())", False),  # Bare helper call: not no_assert
    ("v.validate(run())", True),  # Method call to helper name: still no_assert
])
def test_no_assert_only_suppressed_by_bare_calls(check, expected_no_assert):
    """Only bare function calls with hint words suppress no_assert, not method calls."""
    flags = _flags(f"""
        def _check_output(x):
            pass
        def test_x():
            {check}
        """)
    if expected_no_assert:
        assert "no_assert" in flags["test_x"]
    else:
        assert "no_assert" not in flags["test_x"]


@pytest.mark.parametrize("check", [
    "assert True", "assert out == 42 or True",
    "assert send.assert_called_once_with", "assert send.assert_called_with",
    "assert send.assert_any_call", "assert send.assert_has_calls",
    "assert (out == 5, 'msg')",  # the message inside the parens: a non-empty tuple never fails
    "assert [out == 5]", "assert {out == 5}", "assert {'ok': out == 5}",
    "assert {**d, 'k': 1}", "assert {1: 2, **d}",
])
def test_an_assert_that_is_always_true_is_no_check(check):
    flags = _flags(f"""
        from unittest.mock import patch
        def test_x():
            with patch("mod.send") as send:
                out = run()
            {check}
        """)
    assert "no_assert" in flags["test_x"]


@pytest.mark.parametrize("check", ["assert ()", "assert (*xs,)", "assert [*xs]", "assert {**d}"])
def test_a_display_that_can_be_empty_is_not_always_true(check):
    """`assert ()` fails, and `(*xs,)` fails for an empty `xs`: a check, not a no-op."""
    flags = _flags(f"""
        def test_x(xs, d):
            out = run()
            {check}
        """)
    assert "no_assert" not in flags["test_x"]


def test_an_attribute_named_like_an_assert_method_is_data():
    flags = _flags("""
        def test_x():
            r = run()
            assert r.assert_ok
        """)
    assert "no_assert" not in flags["test_x"]


def test_callable_form_raises_is_a_check():
    flags = _flags("""
        import pytest
        def test_callable_raises():
            pytest.raises(ValueError, int, "bad")
        def test_unused_raises_context_stays_flagged():
            pytest.raises(ValueError)
        """)
    assert "no_assert" not in flags["test_callable_raises"]
    assert "no_assert" in flags["test_unused_raises_context_stays_flagged"]


def test_an_inline_pytest_fail_is_a_check():
    flags = _flags("""
        import pytest
        def test_fail_only():
            if run() != 1:
                pytest.fail("wrong")
        def test_shape_and_fail():
            out = run()
            assert isinstance(out, dict)
            if out != {"a": 1}:
                pytest.fail("wrong")
        """)
    assert flags == {"test_fail_only": (), "test_shape_and_fail": ()}


def test_self_helper_that_asserts_counts_as_a_check():
    flags = _flags("""
        def _check_output(out):
            assert out == 42
        class TestOutput:
            def test_via_self(self):
                self._check_output(run())
            def test_via_unrelated_receiver(self):
                v.validate_output(run())
            def test_same_name_on_unrelated_receiver(self):
                v._check_output(run())
        """)
    assert "no_assert" not in flags["TestOutput.test_via_self"]
    assert "no_assert" in flags["TestOutput.test_via_unrelated_receiver"]
    assert "no_assert" in flags["TestOutput.test_same_name_on_unrelated_receiver"]
