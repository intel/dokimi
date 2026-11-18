# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
"""Identifier vetoes from a C library's API: its header declarations and its exported symbols."""

from __future__ import annotations

import argparse
import hashlib
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from dokimi.eval.reference_checks import grade_check, validate_grading
from dokimi.novelty import symbols
from dokimi.novelty.atoms import Atom, Span, load_atoms_record, save_atoms
from dokimi.novelty.grounding import add_checks, identifiers_check, verify_keys
from dokimi.novelty.symbols import header_names, nm_names, symbol_pattern, symbols_check
from dokimi.utils import InputError

PREFIXES = ("cblas_", "LAPACKE_", "mkl_")

CBLAS_H = """
/* cblas_bogus in a comment declares nothing */
#include <mkl_types.h>
#define CBLAS_INDEX int
void cblas_dgemm(const int order, const int m, const double *a);
double cblas_ddot(const int n, const double *x, const int incx);
const char *banner = "cblas_dgemmx in a string";
"""

LAPACKE_H = "lapack_int LAPACKE_dgesv(int matrix_layout, lapack_int n, double *a);\n"

NM_OUTPUT = """\
0000000000001139 T cblas_dgemm@@MKL_1.0
0000000000001150 T cblas_ddot
0000000000004010 D LAPACKE_dgesv@MKL_1.0
0000000000000000 A Q
0000000000003000 T other_fn
                 U cblas_undefined
"""


def _headers(tmp_path, *, cblas: str = CBLAS_H, lapacke: str | None = LAPACKE_H):
    """A header directory: ``cblas.h`` and, when given, ``lapacke.h``."""
    include = tmp_path / "include"
    include.mkdir(exist_ok=True)
    (include / "cblas.h").write_text(cblas, encoding="utf-8")
    if lapacke is not None:
        (include / "lapacke.h").write_text(lapacke, encoding="utf-8")
    return include


def _check(tmp_path, prefixes=PREFIXES) -> dict:
    return symbols_check(list(prefixes), [str(_headers(tmp_path) / "*.h")], [])


def test_header_names_are_the_prefixed_names_the_code_declares():
    names = header_names(CBLAS_H + LAPACKE_H, ("cblas_", "LAPACKE_", "mkl_"))
    assert names == {"cblas_dgemm", "cblas_ddot", "LAPACKE_dgesv"}


def test_a_header_file_name_is_no_identifier_of_the_prefix():
    # ``mkl_types`` in ``#include <mkl_types.h>`` is a file name, not an API name.
    assert "mkl_types" not in header_names(CBLAS_H, ("mkl_",))


@pytest.mark.parametrize("answer, passed", [
    ("Call `cblas_dgemm(order, m, a)` first.", True),
    ("Call `cblas_dgemmx(order, m, a)` first.", False),  # an invented name
    ("Use `LAPACKE_dgesvv` for the solve.", False),  # the nonexistent LAPACKE name of the study
    ("Use `LAPACKE_dgesv` for the solve.", True),
    ("Link libmkl_rt.so and include mkl_cblas.h.", True),  # not a prefixed name at the start
    ("The cblas word alone names no function.", True),
    ("Use mkl_dfti_unknown here.", False),  # a prefixed name no header declares
    ("There is no cblas_dgemmx in this library.", True),  # a negated mention is no claim
    ("cblas_dgemmx does not exist; use cblas_dgemm.", True),  # a denial right after the name
    ("cblas_dgemm and cblas_dgemmx both work.", False),
])
def test_symbol_check_vetoes_only_prefixed_names_no_header_declares(tmp_path, answer, passed):
    result = grade_check(_check(tmp_path), "q", answer, None)
    assert result.passed is passed
    if not passed:
        assert result.detail.startswith("not in source: ")


def test_a_symbol_check_validates_and_is_never_sufficient(tmp_path):
    check = _check(tmp_path)
    assert validate_grading({"checks": [check]}) == []
    assert validate_grading({"checks": [{**check, "role": "sufficient"}]})


def test_a_key_naming_a_missing_api_name_is_not_found_and_a_real_one_verified(tmp_path):
    check = _check(tmp_path)
    atoms = [{"id": "a1", "statement": "cblas_dgemmx computes the product", "question": "q",
              "answer": "Call cblas_dgemmx.", "meta": None},
             {"id": "a2", "statement": "cblas_ddot computes a dot product", "question": "q",
              "answer": "Call cblas_ddot.", "meta": None}]
    assert add_checks(atoms, [check]) == 2
    assert verify_keys(atoms) == {"verified": 1, "not_found": 1, "unverifiable": 0}
    assert atoms[0]["meta"]["key_status"] == {"status": "not_found", "missing": ["cblas_dgemmx"]}
    assert atoms[1]["meta"]["key_status"] == {"status": "verified"}


def test_the_symbol_check_attaches_to_atoms_naming_a_prefix_and_keeps_tool_checks(tmp_path):
    tool = identifiers_check("perf report", "  --stdio  Use the stdio interface\n")
    atoms = [{"statement": "Call cblas_ddot, then perf report", "question": "q", "answer": "a"},
             {"statement": "The BLAS library is fast", "question": "q", "answer": "a"}]
    assert add_checks(atoms, [tool, _check(tmp_path)]) == 1
    assert add_checks(atoms, [_check(tmp_path)]) == 1  # a re-run replaces its own check
    kinds = [c["source"].get("kind", c["source"].get("tool"))
             for c in atoms[0]["meta"]["grading"]["checks"]]
    assert kinds == ["perf report", "symbols"]
    assert "meta" not in atoms[1]


def test_a_tool_named_symbols_keeps_its_check_when_the_symbol_check_is_replaced(tmp_path):
    tool = identifiers_check("symbols", "  --verbose  Print more\n")
    atoms = [{"statement": "Call cblas_ddot, then run symbols --verbose", "question": "q",
              "answer": "a"}]
    add_checks(atoms, [tool, _check(tmp_path)])
    add_checks(atoms, [_check(tmp_path)])  # a re-run replaces the symbol check only
    sources = [c["source"] for c in atoms[0]["meta"]["grading"]["checks"]]
    assert sorted(s.get("kind", "tool:" + s.get("tool", "")) for s in sources) == [
        "symbols", "tool:symbols"]


def test_symbol_checks_with_other_prefixes_both_survive_a_rerun(tmp_path):
    atoms = [{"statement": "Call cblas_ddot and LAPACKE_dgesv", "question": "q", "answer": "a"}]
    add_checks(atoms, [_check(tmp_path, ("cblas_",))])
    add_checks(atoms, [_check(tmp_path, ("LAPACKE_",))])
    kept = sorted(tuple(c["source"]["prefixes"]) for c in atoms[0]["meta"]["grading"]["checks"])
    assert kept == [("LAPACKE_",), ("cblas_",)]


def test_each_input_is_pinned_by_the_sha256_of_its_bytes(tmp_path):
    check = _check(tmp_path)
    inputs = check["source"]["inputs"]
    assert [i["kind"] for i in inputs] == ["header", "header"]
    header = tmp_path / "include" / "cblas.h"
    assert inputs[0]["sha256"] == hashlib.sha256(header.read_bytes()).hexdigest()
    changed = symbols_check(["cblas_"], [str(tmp_path / "include" / "cblas.h")], [])
    (tmp_path / "include" / "cblas.h").write_text(CBLAS_H + "/* edit */\n", encoding="utf-8")
    again = symbols_check(["cblas_"], [str(tmp_path / "include" / "cblas.h")], [])
    assert again["source"]["inputs"][0]["sha256"] != changed["source"]["inputs"][0]["sha256"]


def test_known_names_are_the_prefixed_names_of_all_inputs(tmp_path):
    check = _check(tmp_path)
    assert check["known"] == ["LAPACKE_dgesv", "cblas_ddot", "cblas_dgemm"]


@pytest.mark.parametrize("prefix", ["mkl", "cblas-", "1_", "", "x y_", "_"])
def test_a_prefix_must_be_a_c_identifier_ending_in_an_underscore(tmp_path, prefix):
    with pytest.raises(InputError, match="prefix"):
        symbols_check([prefix], [str(_headers(tmp_path) / "*.h")], [])


def test_a_header_glob_expands_to_sorted_files_and_a_pattern_matching_none_is_refused(tmp_path):
    check = _check(tmp_path)
    names = [Path(i["path"]).name for i in check["source"]["inputs"]]
    assert names == ["cblas.h", "lapacke.h"]
    with pytest.raises(InputError, match="matches no file"):
        symbols_check(["cblas_"], [str(tmp_path / "nowhere" / "*.h")], [])


def test_a_header_set_with_no_prefixed_name_is_refused_so_nothing_is_vetoed_by_mistake(tmp_path):
    with pytest.raises(InputError, match="no name with the prefix"):
        symbols_check(["mkl_"], [str(_headers(tmp_path, lapacke=None) / "cblas.h")], [])


def test_nm_names_reads_defined_symbols_and_drops_their_version_tags():
    assert nm_names(NM_OUTPUT, ("cblas_", "LAPACKE_")) == {"cblas_dgemm", "cblas_ddot",
                                                           "LAPACKE_dgesv"}


def test_library_names_are_the_exported_symbols_of_a_shared_library(tmp_path):
    if shutil.which("nm") is None or shutil.which("cc") is None:
        pytest.skip("needs nm and cc to build a fixture library")
    source = tmp_path / "fixture.c"
    source.write_text("int cblas_dgemm(int x) { return x; }\n"
                      "int cblas_ddot(int x) { return x; }\n"
                      "int other_fn(int x) { return x; }\n"
                      "static int cblas_hidden(int x) { return x; }\n", encoding="utf-8")
    library = tmp_path / "libfixture.so"
    subprocess.run(["cc", "-shared", "-fPIC", "-o", str(library), str(source)], check=True)
    check = symbols_check(["cblas_"], [], [str(library)])
    assert check["known"] == ["cblas_ddot", "cblas_dgemm"]
    assert check["source"]["inputs"][0]["kind"] == "library"


def test_a_missing_nm_is_an_input_error_that_names_it(tmp_path, monkeypatch):
    def no_nm(*args, **kwargs):
        raise FileNotFoundError("nm")
    monkeypatch.setattr(symbols, "run", no_nm)
    library = tmp_path / "libfixture.so"
    library.write_bytes(b"not read")
    with pytest.raises(InputError, match="nm is not installed"):
        symbols_check(["cblas_"], [], [str(library)])


def test_a_file_nm_cannot_read_is_an_input_error(tmp_path):
    if shutil.which("nm") is None:
        pytest.skip("needs nm")
    library = tmp_path / "libnot.so"
    library.write_text("this is text, not an object file\n", encoding="utf-8")
    with pytest.raises(InputError, match="nm cannot read"):
        symbols_check(["cblas_"], [], [str(library)])


def test_no_prefix_at_all_is_refused_because_an_empty_pattern_names_every_word(tmp_path):
    with pytest.raises(InputError, match="prefix"):
        symbols_check([], [str(_headers(tmp_path) / "*.h")], [])


def test_an_unreadable_header_is_an_input_error_not_a_traceback(tmp_path, monkeypatch):
    header = _headers(tmp_path) / "cblas.h"

    def denied(self, *args, **kwargs):
        raise PermissionError(13, "Permission denied")
    monkeypatch.setattr(Path, "read_text", denied)
    with pytest.raises(InputError, match="cannot read the header"):
        symbols_check(["cblas_"], [str(header)], [])


def _atoms_file(tmp_path, answer="Call cblas_dgemm."):
    span = Span(id="s1", kind="prose", text="cblas_dgemm is the product", start=0, end=25)
    path = tmp_path / "atoms.json"
    save_atoms([Atom(statement="cblas_dgemm is the product", question="How?", answer=answer,
                     span_ids=("s1",))], [span], path)
    return path


def test_novelty_ground_writes_the_symbol_check_and_verifies_the_key(tmp_path, capsys):
    from dokimi.commands import novelty_ground
    atoms_path = _atoms_file(tmp_path)
    args = argparse.Namespace(atoms=str(atoms_path), out=None, tool=None, tool_version=None,
                              header=[str(_headers(tmp_path) / "*.h")], shared_lib=[],
                              prefix=["cblas_", "LAPACKE_"])
    novelty_ground.cmd_novelty_ground(args)
    _, _, data = load_atoms_record(atoms_path)
    atom = data["atoms"][0]
    assert atom["meta"]["grading"]["checks"][0]["source"]["kind"] == "symbols"
    assert atom["meta"]["key_status"] == {"status": "verified"}
    err = capsys.readouterr().err
    assert "symbols" in err and "1 of 1 atoms got a veto check" in err


@pytest.mark.parametrize("kwargs, message", [
    ({"tool": None, "header": [], "shared_lib": [], "prefix": []}, "--tool, --header or --shared-lib"),
    ({"tool": None, "header": ["x.h"], "shared_lib": [], "prefix": []}, "--prefix"),
    ({"tool": None, "header": [], "shared_lib": [], "prefix": ["cblas_"]}, "--prefix scopes"),
])
def test_novelty_ground_refuses_symbol_flags_that_do_not_go_together(tmp_path, kwargs, message):
    from dokimi.commands import novelty_ground
    args = argparse.Namespace(atoms=str(_atoms_file(tmp_path)), out=None, tool_version=None,
                              **kwargs)
    with pytest.raises(InputError, match=message):
        novelty_ground.cmd_novelty_ground(args)


def test_the_shared_library_flag_is_shared_lib_and_library_is_refused():
    from dokimi import cli
    parser = cli.build_parser()
    args = parser.parse_args(["skill", "novelty", "ground", "--atoms", "a.json", "--shared-lib", "libx.so",
                              "--prefix", "cblas_"])
    assert args.shared_lib == ["libx.so"]
    with pytest.raises(SystemExit):
        parser.parse_args(["skill", "novelty", "ground", "--atoms", "a.json", "--library", "libx.so",
                           "--prefix", "cblas_"])


def test_a_header_path_with_glob_characters_that_exists_is_read(tmp_path):
    include = tmp_path / "inc[1]"
    include.mkdir()
    (include / "cblas.h").write_text(CBLAS_H, encoding="utf-8")
    check = symbols_check(["cblas_"], [str(include / "cblas.h")], [])
    assert "cblas_dgemm" in check["known"]


def test_a_line_comment_continued_by_a_backslash_declares_no_name():
    text = "void cblas_real(void);\n// unavailable APIs: \\\n cblas_fake\n"
    assert header_names(text, ("cblas_",)) == {"cblas_real"}


def test_a_library_path_starting_with_a_dash_is_read_as_a_file(tmp_path, monkeypatch):
    if shutil.which("nm") is None or shutil.which("cc") is None:
        pytest.skip("needs nm and cc to build a fixture library")
    source = tmp_path / "fixture.c"
    source.write_text("int cblas_ddot(int x) { return x; }\n", encoding="utf-8")
    subprocess.run(["cc", "-shared", "-fPIC", "-o", str(tmp_path / "-api.so"), str(source)],
                   check=True)
    monkeypatch.chdir(tmp_path)
    check = symbols_check(["cblas_"], [], ["./-api.so"])
    assert check["known"] == ["cblas_ddot"]


def test_an_overlapping_prefix_set_replaces_the_stale_symbol_check(tmp_path):
    old_dir = tmp_path / "old"
    old_dir.mkdir()
    (old_dir / "cblas.h").write_text("void cblas_ddot(void);\n", encoding="utf-8")
    old = symbols_check(["cblas_"], [str(old_dir / "cblas.h")], [])
    atoms = [{"statement": "Call cblas_dgemm.", "question": "q", "answer": "a"}]
    add_checks(atoms, [old])
    add_checks(atoms, [_check(tmp_path, ("LAPACKE_", "cblas_"))])
    checks = atoms[0]["meta"]["grading"]["checks"]
    assert [tuple(c["source"]["prefixes"]) for c in checks] == [("LAPACKE_", "cblas_")]


def test_a_nested_prefix_refresh_keeps_the_broader_check_for_its_other_scopes(tmp_path):
    old_dir = tmp_path / "old"
    old_dir.mkdir()
    (old_dir / "mkl.h").write_text("void mkl_sparse_old(void);\nvoid mkl_vml_add(void);\n"
                                   "void mkl_sparse_gone(void);\n", encoding="utf-8")
    old = symbols_check(["mkl_"], [str(old_dir / "mkl.h")], [])
    atoms = [{"statement": "Call mkl_sparse_new.", "question": "q", "answer": "a"}]
    add_checks(atoms, [old])
    new_dir = tmp_path / "new"
    new_dir.mkdir()
    (new_dir / "mkl.h").write_text("void mkl_sparse_old(void);\nvoid mkl_sparse_new(void);\n", encoding="utf-8")
    add_checks(atoms, [symbols_check(["mkl_sparse_"], [str(new_dir / "mkl.h")], [])])
    checks = atoms[0]["meta"]["grading"]["checks"]
    assert [tuple(c["source"]["prefixes"]) for c in checks] == [("mkl_",), ("mkl_sparse_",)]
    broad = checks[0]
    # the unaffected scope keeps its names; the refreshed scope takes the new header's names
    assert broad["known"] == ["mkl_sparse_new", "mkl_sparse_old", "mkl_vml_add"]
    # no veto on a newly declared name, nor on one only the broader header declared
    for answer in ("Call mkl_sparse_new().", "Call mkl_vml_add()."):
        assert all(grade_check(c, "q", answer, None).passed for c in checks), answer


def test_a_same_path_overwrite_keeps_the_old_snapshot_too(tmp_path):
    header = tmp_path / "mkl.h"
    header.write_text("void mkl_sparse_old(void);\nvoid mkl_vml_add(void);\n", encoding="utf-8")
    atoms = [{"statement": "Call mkl_sparse_new.", "question": "q", "answer": "a"}]
    add_checks(atoms, [symbols_check(["mkl_"], [str(header)], [])])
    header.write_text("void mkl_sparse_new(void);\n", encoding="utf-8")  # the same path, overwritten
    add_checks(atoms, [symbols_check(["mkl_sparse_"], [str(header)], [])])
    broad = atoms[0]["meta"]["grading"]["checks"][0]
    assert "mkl_vml_add" in broad["known"]  # kept: its only pinned snapshot is the old header
    # both snapshots of the path stay pinned while a retained name depends on the old one
    assert len({i["sha256"] for i in broad["source"]["inputs"] if i["path"] == str(header)}) == 2

@pytest.mark.parametrize("code", [
    "return cblas_ddot(n,x,1)-cblas_dgemmx(n,x,1);",
    "return cblas_ddot(n,x,1)/cblas_dgemmx(n,x,1);",
    "if (x>cblas_dgemmx(n,x,1)) return 0;",
    "return a/cblas_dgemmx(n,x,1);",
    "return cblas_ddot(n,x,1)/cblas_dgemmx (n,x,1);",
    "return cblas_ddot(n,x,1)/cblas_dgemmx\n(n,x,1);",
    "return a/cblas_dgemmx\t(n,x,1);",
    "Return x -cblas_dgemmx;",
    "Return f(x) -cblas_dgemmx;",
])
def test_an_operator_before_a_called_api_name_keeps_the_name(code):
    assert "cblas_dgemmx" in re.findall(symbol_pattern(("cblas_",)), code)


def test_a_called_api_name_with_whitespace_before_the_paren_is_in_the_key_scope():
    # The bot's case: an invented call after an operator, with a space before its argument list.
    from dokimi.novelty.symbols import header_names
    assert header_names("return cblas_ddot(n,x,1)/cblas_dgemmx (n,x,1);", ("cblas_",)) == {"cblas_ddot", "cblas_dgemmx"}


@pytest.mark.parametrize("code, name", [
    ("x = s.cblas_dgemmx;", "cblas_dgemmx"),
    ("x = p->cblas_dgemmx;", "cblas_dgemmx"),
    ("x = p-> cblas_dgemmx;", "cblas_dgemmx"),
    ("run --cblas_dgemmx", "cblas_dgemmx"),
    ("inc/cblas_api", "cblas_api"),
])
def test_a_member_option_or_path_is_not_an_api_name(code, name):
    assert name not in re.findall(symbol_pattern(("cblas_",)), code)


@pytest.mark.parametrize("text", ["Use the API. cblas_dgemmx handles it.", "Done.\ncblas_dgemmx(a);", "1. cblas_dgemmx"])
def test_a_name_after_a_period_in_prose_is_an_api_name(text):
    assert "cblas_dgemmx" in re.findall(symbol_pattern(("cblas_",)), text)


def test_a_narrower_old_check_is_replaced_by_the_broader_new_one(tmp_path):
    old_dir = tmp_path / "old"
    old_dir.mkdir()
    (old_dir / "mkl.h").write_text("void mkl_sparse_old(void);\n", encoding="utf-8")
    old = symbols_check(["mkl_sparse_"], [str(old_dir / "mkl.h")], [])
    atoms = [{"statement": "Call mkl_sparse_old.", "question": "q", "answer": "a"}]
    add_checks(atoms, [old])
    new_dir = tmp_path / "new"
    new_dir.mkdir()
    (new_dir / "mkl.h").write_text("void mkl_sparse_old(void);\nvoid mkl_vml_add(void);\n", encoding="utf-8")
    add_checks(atoms, [symbols_check(["mkl_"], [str(new_dir / "mkl.h")], [])])
    checks = atoms[0]["meta"]["grading"]["checks"]
    assert [tuple(c["source"]["prefixes"]) for c in checks] == [("mkl_",)]


def test_a_refresh_leaves_a_check_shared_with_another_atom_unchanged(tmp_path):
    old_dir = tmp_path / "old"
    old_dir.mkdir()
    (old_dir / "mkl.h").write_text("void mkl_sparse_old(void);\nvoid mkl_vml_add(void);\n", encoding="utf-8")
    old = symbols_check(["mkl_"], [str(old_dir / "mkl.h")], [])
    before = repr(old)
    atoms = [{"statement": "Call mkl_sparse_new.", "question": "q", "answer": "a"},
             {"statement": "Call mkl_vml_add.", "question": "q", "answer": "a"}]
    add_checks(atoms, [old])
    new_dir = tmp_path / "new"
    new_dir.mkdir()
    (new_dir / "mkl.h").write_text("void mkl_sparse_new(void);\n", encoding="utf-8")
    new = symbols_check(["mkl_sparse_"], [str(new_dir / "mkl.h")], [])
    add_checks([atoms[0]], [new])  # only the first atom names the narrower scope
    assert repr(old) == before  # the check the second atom still holds is not rewritten
    assert atoms[1]["meta"]["grading"]["checks"][0]["known"] == ["mkl_sparse_old", "mkl_vml_add"]


def test_a_refresh_that_leaves_an_old_check_with_no_names_drops_it(tmp_path):
    old_dir = tmp_path / "old"
    old_dir.mkdir()
    (old_dir / "mkl.h").write_text("void mkl_sparse_a(void);\n", encoding="utf-8")
    old = symbols_check(["mkl_sparse_", "cblas_"], [str(old_dir / "mkl.h")], [])
    atoms = [{"statement": "Call mkl_sparse_a.", "question": "q", "answer": "a"}]
    add_checks(atoms, [old])
    new_dir = tmp_path / "new"
    new_dir.mkdir()
    (new_dir / "mkl.h").write_text("void mkl_vml_add(void);\n", encoding="utf-8")
    add_checks(atoms, [symbols_check(["mkl_"], [str(new_dir / "mkl.h")], [])])
    checks = atoms[0]["meta"]["grading"]["checks"]
    assert [tuple(c["source"]["prefixes"]) for c in checks] == [("mkl_",)]
    assert all(c["known"] for c in checks)


def test_an_apostrophe_in_a_directive_text_does_not_hide_the_names_between_two_of_them():
    # Two apostrophes on one line are not a character literal: the names between them are the header's.
    from dokimi.novelty.symbols import header_names
    text = "#error Don't call cblas_dgemm_batch directly; include mkl's header\n"
    assert "cblas_dgemm_batch" in header_names(text, ("cblas_",))
    assert header_names("char c = '\\n'; int cblas_dgemm;", ("cblas_",)) == {"cblas_dgemm"}


def test_a_long_answer_with_many_mentions_is_scanned_in_linear_time():
    # Every mention used to re-read the whole text before it: 126 KB took about 12 s, past the grader's 1 s.
    import time
    from dokimi.eval.reference_checks import scope_pairs
    unit = "Then call cblas_dgemm(a, b) on the block. "
    answer = (unit * 3000)[:126_000]
    started = time.monotonic()
    assert len(scope_pairs(symbol_pattern(("cblas_",)), answer, timeout=30)) > 2000
    assert time.monotonic() - started < 5.0  # a quadratic scan takes 11 s here; the box is shared


def test_a_refresh_with_a_subset_of_the_prefixes_does_not_veto_the_same_names_twice(tmp_path):
    atoms = [{"statement": "Call cblas_dgemm and LAPACKE_dgesv.", "question": "q", "answer": "a"}]
    add_checks(atoms, [symbols_check(["cblas_", "LAPACKE_"], [str(_headers(tmp_path) / "*.h")], [])])
    add_checks(atoms, [symbols_check(["cblas_"], [str(_headers(tmp_path) / "*.h")], [])])
    checks = atoms[0]["meta"]["grading"]["checks"]
    assert [sorted(c["source"]["prefixes"]) for c in checks] == [["LAPACKE_", "cblas_"]]  # one check, not two
    assert "cblas_dgemm" in checks[0]["known"] and "LAPACKE_dgesv" in checks[0]["known"]


def test_a_tool_version_without_a_tool_is_refused_not_dropped():
    from dokimi.commands.novelty_ground import _refuse_bad_sources
    with pytest.raises(InputError, match="--tool-version"):
        _refuse_bad_sources([], ["cblas.h"], [], ["cblas_"], "2025.1")


MKL_H = "void mkl_set_num_threads(int n);\n"


@pytest.mark.parametrize("answer", [
    "Link with mkl_intel_lp64, mkl_sequential and mkl_core.",  # a link sentence names library files
    "Use the mkl_rt single dynamic library.",  # a library file named in a sentence about the library
    "target_link_libraries(app MKL::mkl_core)",  # a CMake link operand, a namespace-scoped library
    "Add -l mkl_core to the link line.",  # a -l operand
])
def test_library_names_in_link_context_are_not_vetoed_under_a_shared_prefix(tmp_path, answer):
    check = symbols_check(["mkl_"], [str(_mkl_header(tmp_path))], [])
    result = grade_check(check, "q", answer, None)
    assert result.passed is True, result.detail


@pytest.mark.parametrize("answer", [
    "Call mkl_bogus_fn(1) to set the threads.",  # a call is an API claim, link words or not
    "Use mkl_bogus_fn to set the threads.",  # a prefixed name no header declares, no link context
    "Link against mkl_bogus_fn(1) for the threads.",  # a called name stays vetoed in a link sentence
])
def test_a_real_missing_symbol_stays_vetoed_under_a_shared_prefix(tmp_path, answer):
    check = symbols_check(["mkl_"], [str(_mkl_header(tmp_path))], [])
    result = grade_check(check, "q", answer, None)
    assert result.passed is False
    assert result.detail == "not in source: mkl_bogus_fn"


def test_a_shared_library_stem_with_the_prefix_is_a_known_name_not_a_symbol(tmp_path):
    if shutil.which("nm") is None or shutil.which("cc") is None:
        pytest.skip("needs nm and cc to build a fixture library")
    source = tmp_path / "fixture.c"
    source.write_text("int mkl_set_num_threads(int n) { return n; }\n", encoding="utf-8")
    library = tmp_path / "libmkl_rt.so"
    subprocess.run(["cc", "-shared", "-fPIC", "-o", str(library), str(source)], check=True)
    check = symbols_check(["mkl_"], [], [str(library)])
    assert "mkl_rt" in check["known"]  # the stem of libmkl_rt.so, from its file name
    result = grade_check(check, "q", "Load mkl_rt before the solve.", None)
    assert result.passed is True, result.detail


def _mkl_header(tmp_path):
    include = tmp_path / "include"
    include.mkdir(exist_ok=True)
    (include / "mkl.h").write_text(MKL_H, encoding="utf-8")
    return include / "mkl.h"
