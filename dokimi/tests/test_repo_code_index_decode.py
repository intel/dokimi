# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The SCIP decoder on hand-built bytes: unknown fields, bad input, truncation, and symbol strings.

The encoder below writes the scip.proto field numbers the reader uses, with protobuf wire encoding.
Unknown fields use numbers of 40 and up, so a later scip.proto field cannot collide with them.
The checks on a real index are in ``test_repo_code_index.py``.
"""

from __future__ import annotations

import random
from pathlib import Path

import pytest

from dokimi.repos.code_index import load
from dokimi.repos.code_index.index import CodeIndex
from dokimi.repos.code_index.scip import decode_index
from dokimi.repos.code_index.symbols import descriptor_name
from dokimi.repos.code_index.wire import read_varint
from dokimi.repos.errors import CodeIndexError

SYM = "scip-test python pkg 1.0 `m`/f()."
BASE_SYM = "scip-test python pkg 1.0 `m`/Base#"
PKG = "scip-test python pkg 1.0 "


def varint(n: int) -> bytes:
    out = bytearray()
    while True:
        low = n & 0x7F
        n >>= 7
        if n:
            out.append(low | 0x80)
        else:
            out.append(low)
            return bytes(out)


def key(field: int, wire: int) -> bytes:
    return varint(field << 3 | wire)


def ld(field: int, body: bytes) -> bytes:
    return key(field, 2) + varint(len(body)) + body


def vint(field: int, value: int) -> bytes:
    return key(field, 0) + varint(value)


def ints(field: int, values: list[int], packed: bool = True) -> bytes:
    if packed:
        return ld(field, b"".join(varint(v) for v in values))
    return b"".join(vint(field, v) for v in values)


def occurrence(rng: list[int], symbol: str = "", roles: int = 0, enclosing: list[int] | None = None,
               packed: bool = True, extra: bytes = b"") -> bytes:
    body = ints(1, rng, packed)
    if symbol:
        body += ld(2, symbol.encode())
    if roles:
        body += vint(3, roles)
    if enclosing is not None:
        body += ints(7, enclosing, packed)
    return body + extra


def relationship(target: str, is_implementation: bool, extra: bytes = b"") -> bytes:
    return ld(1, target.encode()) + (vint(3, 1) if is_implementation else b"") + extra


def symbol_info(symbol: str, implements: tuple[str, ...] = (), extra: bytes = b"", rel_extra: bytes = b"") -> bytes:
    body = ld(1, symbol.encode()) + b"".join(ld(4, relationship(t, True, rel_extra)) for t in implements)
    return ld(3, body + extra)


def document(path: str, occurrences: tuple[bytes, ...] = (), symbols: bytes = b"", extra: bytes = b"") -> bytes:
    return ld(2, ld(1, path.encode()) + b"".join(ld(2, o) for o in occurrences) + symbols + extra)


def metadata(tool_extra: bytes = b"", extra: bytes = b"") -> bytes:
    tool = ld(1, b"scip-test") + ld(2, b"0.1") + tool_extra
    return ld(1, ld(2, tool) + ld(3, b"/project/root") + vint(4, 1) + extra)


def index(*documents: bytes, external: bytes = b"", extra: bytes = b"", meta: bytes | None = None) -> bytes:
    return (metadata() if meta is None else meta) + b"".join(documents) + external + extra


def _index(data: bytes) -> CodeIndex:
    return CodeIndex(decode_index(memoryview(data)))


def _load(tmp_path: Path, data: bytes, root_prefix: str = "") -> CodeIndex:
    path = tmp_path / "index.scip"
    path.write_bytes(data)
    return load(path, root_prefix=root_prefix)


def test_a_hand_built_index_decodes_each_field(tmp_path):
    ix = _load(tmp_path, index(document("a.py", (occurrence([1, 2, 5], SYM, roles=1, enclosing=[1, 0, 9, 0]),))))
    assert ix.files() == ("a.py",)
    assert ix.tool == "scip-test 0.1"
    (defn,) = ix.definitions("f")
    assert (defn.path, defn.line, defn.start_col, defn.end_col) == ("a.py", 1, 2, 5)
    assert defn.symbol == SYM and defn.is_definition
    assert defn.enclosing == (1, 8)
    assert ix.has_enclosing_ranges


def test_packed_and_unpacked_repeated_ints_decode_the_same(tmp_path):
    packed = index(document("a.py", (occurrence([300, 2, 5], SYM, enclosing=[290, 0, 310, 0]),)))
    unpacked = index(document("a.py", (occurrence([300, 2, 5], SYM, enclosing=[290, 0, 310, 0], packed=False),)))
    assert _index(packed).occurrences("a.py") == _index(unpacked).occurrences("a.py")
    assert _index(packed).occurrences("a.py")[0].enclosing == (290, 309)


def test_a_three_value_range_is_on_one_line_and_encloses_that_line(tmp_path):
    ix = _index(index(document("a.py", (occurrence([4, 1, 7], SYM, enclosing=[4, 0, 8]),))))
    (occ,) = ix.occurrences("a.py")
    assert (occ.line, occ.start_col, occ.end_col, occ.enclosing) == (4, 1, 7, (4, 4))


def test_unknown_fields_at_every_level_are_skipped():
    plain = index(
        document("a.py", (occurrence([1, 2, 5], SYM, roles=1, enclosing=[1, 0, 9, 0]),),
                 symbols=symbol_info(SYM, (BASE_SYM,))),
        external=symbol_info(BASE_SYM),
    )
    noisy = index(
        document(
            "a.py",
            (occurrence([1, 2, 5], SYM, roles=1, enclosing=[1, 0, 9, 0],
                        extra=vint(40, 1) + key(41, 1) + b"\x00" * 8 + ld(42, b"x")),),
            symbols=symbol_info(SYM, (BASE_SYM,), extra=vint(40, 1) + key(41, 5) + b"\x00" * 4,
                                rel_extra=vint(40, 1)),
            extra=ld(40, b"x") + vint(41, 3),
        ),
        external=symbol_info(BASE_SYM, extra=ld(40, b"x")),
        extra=vint(40, 7) + key(41, 1) + b"\x01" * 8 + key(42, 5) + b"\x02" * 4 + ld(43, b"newer"),
        meta=metadata(tool_extra=ld(3, b"--arg"), extra=vint(40, 1)),
    )
    a, b = _index(plain), _index(noisy)
    assert b.files() == a.files() == ("a.py",)
    assert b.occurrences("a.py") == a.occurrences("a.py")
    assert b.bases(SYM) == a.bases(SYM) == (BASE_SYM,)
    assert b.tool == a.tool == "scip-test 0.1"


def test_a_known_field_with_the_wrong_wire_type_is_skipped():
    # Occurrence.symbol written as a varint is skipped like an unknown field: the occurrence has no symbol
    occ = ints(1, [1, 2, 5]) + vint(2, 9)
    (only,) = _index(index(document("a.py", (occ,)))).occurrences("a.py")
    assert (only.line, only.symbol) == (1, "")


def test_only_is_implementation_relationships_are_bases():
    info = ld(3, ld(1, SYM.encode()) + ld(4, relationship(BASE_SYM, False)))
    assert _index(index(document("a.py", symbols=info))).bases(SYM) == ()


def test_a_relationship_without_a_target_is_ignored():
    info = ld(3, ld(1, SYM.encode()) + ld(4, vint(3, 1)))
    assert _index(index(document("a.py", symbols=info))).bases(SYM) == ()


def test_a_local_id_is_never_recorded_as_a_base():
    data = index(document("a.py", symbols=symbol_info("local 2", (BASE_SYM,))))
    assert decode_index(memoryview(data)).bases == {}


@pytest.mark.parametrize(("meta", "tool"), [
    (metadata(), "scip-test 0.1"),
    (ld(1, ld(2, ld(1, b"scip-test"))), "scip-test"),
    (ld(1, vint(4, 1)), ""),
])
def test_the_tool_is_name_and_version_or_empty(meta, tool):
    assert _index(index(meta=meta)).tool == tool


def test_root_prefix_is_put_once_before_every_document_path(tmp_path):
    data = index(document("a.py"), document("pkg/b.py"))
    expected = ("dokimi/a.py", "dokimi/pkg/b.py")
    assert _load(tmp_path, data, root_prefix="dokimi").files() == expected
    assert _load(tmp_path, data, root_prefix="dokimi/").files() == expected


def test_a_local_id_in_two_documents_names_two_symbols():
    data = index(document("a.py", (occurrence([0, 0, 1], "local 1", roles=1),)),
                 document("b.py", (occurrence([3, 0, 1], "local 1", roles=1),)))
    ix = _index(data)
    assert [o.line for o in ix.occurrences("a.py")] == [0]
    assert [o.line for o in ix.occurrences("b.py")] == [3]
    with pytest.raises(ValueError, match="local id"):
        ix.references("local 1")


def test_bases_in_external_symbols_are_read():
    ix = _index(index(document("a.py"), external=symbol_info(SYM, (BASE_SYM,))))
    assert ix.bases(SYM) == (BASE_SYM,)


def test_an_index_without_ranges_has_no_enclosing_scope():
    ix = _index(index(document("a.py", (occurrence([1, 2, 5], SYM, roles=1),))))
    assert not ix.has_enclosing_ranges
    assert ix.enclosing("a.py", 1) is None


def test_a_four_value_range_keeps_its_end_line():
    (occ,) = _index(index(document("a.py", (occurrence([10, 4, 14, 1], SYM),)))).occurrences("a.py")
    assert (occ.line, occ.start_col, occ.end_line, occ.end_col) == (10, 4, 14, 1)
    (one,) = _index(index(document("a.py", (occurrence([4, 1, 7], SYM),)))).occurrences("a.py")
    assert (one.line, one.end_line, one.end_col) == (4, 4, 7)


def test_an_identical_repeated_occurrence_is_kept_once():
    ref = occurrence([3, 0, 1], SYM)
    defn = occurrence([1, 2, 5], SYM, roles=1)
    ix = _index(index(document("a.py", (defn, ref, defn, ref, occurrence([3, 4, 5], SYM)))))
    assert [o.line for o in ix.occurrences("a.py")] == [1, 3, 3]
    assert [(o.line, o.start_col) for o in ix.references(SYM)] == [(3, 0), (3, 4)]
    assert len(ix.definitions("f")) == 1


def test_the_same_path_in_two_documents_is_one_file():
    ix = _index(index(document("a.py", (occurrence([1, 0, 1], SYM),)),
                      document("a.py", (occurrence([2, 0, 1], SYM),))))
    assert ix.files() == ("a.py",)
    assert [o.line for o in ix.occurrences("a.py")] == [1, 2]


def test_files_are_sorted_whatever_the_document_order():
    assert _index(index(document("b.py"), document("a.py"))).files() == ("a.py", "b.py")


def test_enclosing_is_half_open_so_an_end_at_column_zero_excludes_that_line():
    ix = _index(index(document("a.py", (occurrence([1, 2, 5], SYM, roles=1, enclosing=[1, 0, 9, 0]),))))
    assert ix.enclosing("a.py", 1).symbol == SYM
    assert ix.enclosing("a.py", 8).symbol == SYM
    assert ix.enclosing("a.py", 9) is None


def test_enclosing_ending_after_column_zero_includes_its_end_line():
    ix = _index(index(document("a.py", (occurrence([1, 2, 5], SYM, roles=1, enclosing=[1, 0, 9, 4]),))))
    assert ix.occurrences("a.py")[0].enclosing == (1, 9)
    assert ix.enclosing("a.py", 9).symbol == SYM


def test_an_empty_enclosing_range_holds_no_line():
    ix = _index(index(document("a.py", (occurrence([4, 3, 3], SYM, roles=1, enclosing=[4, 3, 4, 3]),))))
    assert ix.enclosing("a.py", 4) is None


@pytest.mark.parametrize("order", [0, 1])
def test_the_inner_of_two_equal_line_spans_is_the_one_nested_by_columns(order):
    outer = occurrence([1, 0, 3], "outer", roles=1, enclosing=[1, 0, 9, 20])
    inner = occurrence([1, 15, 18], "inner", roles=1, enclosing=[1, 15, 9, 10])
    occs = (outer, inner) if order == 0 else (inner, outer)
    ix = _index(index(document("a.py", occs)))
    assert ix.enclosing("a.py", 5).symbol == "inner"


def test_a_local_definition_keeps_its_enclosing_scope():
    ix = _index(index(document("a.py", (occurrence([1, 2, 5], "local 1", roles=1, enclosing=[1, 0, 9, 0]),))))
    assert ix.has_enclosing_ranges
    scope = ix.enclosing("a.py", 4)
    assert scope is not None and scope.symbol == "local 1"


@pytest.mark.parametrize("rng", [[1, 5, 2], [3, 0, 2, 9], [1, 0, 2**31], [2**31, 0, 2**31, 1]])
def test_an_occurrence_span_must_be_ordered_and_fit_int32(rng):
    with pytest.raises(CodeIndexError):
        _index(index(document("a.py", (occurrence(rng, SYM, roles=1),))))


@pytest.mark.parametrize("enc", [[4, 5, 2], [5, 0, 4, 0], [1, 0, 2**31]])
def test_an_enclosing_span_must_be_ordered_and_fit_int32(enc):
    with pytest.raises(CodeIndexError):
        _index(index(document("a.py", (occurrence([1, 2, 5], SYM, roles=1, enclosing=enc),))))


def test_a_zero_width_span_is_valid():
    ix = _index(index(document("a.py", (occurrence([4, 3, 3], SYM, roles=1, enclosing=[4, 3, 4, 3]),))))
    (occ,) = ix.occurrences("a.py")
    assert (occ.line, occ.start_col, occ.end_col) == (4, 3, 3)


def typed(field: int, *values: int) -> bytes:
    """A typed range message: SingleLineRange (fields 8 and 10) or MultiLineRange (9 and 11)."""
    return ld(field, b"".join(vint(i + 1, v) for i, v in enumerate(values)))


def test_typed_ranges_alone_decode():
    single = _index(index(document("a.py", (occurrence([], SYM, roles=1, extra=typed(8, 4, 1, 7)
                                                    + typed(10, 4, 0, 8)),))))
    (occ,) = single.occurrences("a.py")
    assert (occ.line, occ.start_col, occ.end_line, occ.end_col, occ.enclosing) == (4, 1, 4, 7, (4, 4))
    multi = _index(index(document("a.py", (occurrence([], SYM, roles=1, extra=typed(9, 1, 0, 3, 4)
                                                   + typed(11, 1, 0, 9, 0)),))))
    (occ,) = multi.occurrences("a.py")
    assert (occ.line, occ.start_col, occ.end_line, occ.end_col, occ.enclosing) == (1, 0, 3, 4, (1, 8))


def test_typed_ranges_take_precedence_over_the_deprecated_ones():
    both = occurrence([2, 0, 5], SYM, roles=1, enclosing=[1, 0, 9, 0], extra=typed(8, 4, 1, 7) + typed(10, 4, 0, 8))
    (occ,) = _index(index(document("a.py", (both,)))).occurrences("a.py")
    assert (occ.line, occ.start_col, occ.end_col, occ.enclosing) == (4, 1, 7, (4, 4))


def test_a_split_typed_range_merges_as_protobuf_does():
    single = occurrence([], SYM, roles=1, extra=typed(8, 4, 1) + ld(8, vint(3, 7)))
    (occ,) = _index(index(document("a.py", (single,)))).occurrences("a.py")
    assert (occ.line, occ.start_col, occ.end_line, occ.end_col) == (4, 1, 4, 7)
    multi = occurrence([], SYM, roles=1, extra=typed(9, 1, 0, 3) + ld(9, vint(4, 9)))
    (occ,) = _index(index(document("a.py", (multi,)))).occurrences("a.py")
    assert (occ.line, occ.start_col, occ.end_line, occ.end_col) == (1, 0, 3, 9)
    enclosing = occurrence([], SYM, roles=1, extra=typed(8, 4, 1, 3) + typed(10, 4, 0, 8) + ld(10, vint(3, 9)))
    (occ,) = _index(index(document("a.py", (enclosing,)))).occurrences("a.py")
    assert occ.enclosing == (4, 4) and occ.enclosing_span == (4, 0, 4, 9)


def test_a_split_tool_info_merges_name_and_version_across_metadata_messages():
    meta = ld(1, ld(2, ld(1, b"scip-test"))) + ld(1, ld(2, ld(2, b"0.1")))
    assert _index(index(meta=meta)).tool == "scip-test 0.1"
    later = ld(1, ld(2, ld(1, b"scip-test") + ld(2, b"0.1"))) + ld(1, ld(2, ld(2, b"0.2")))
    assert _index(index(meta=later)).tool == "scip-test 0.2"


def test_the_largest_field_number_is_legal_and_the_next_one_is_not():
    largest = metadata() + key(2**29 - 1, 0) + varint(0)
    assert _index(largest).tool == "scip-test 0.1"
    with pytest.raises(CodeIndexError, match="is above the protobuf maximum"):
        _index(metadata() + key(2**29, 0) + varint(0))


def test_a_varint_of_ten_bytes_must_fit_in_64_bits():
    assert read_varint(memoryview(b"\xff" * 9 + b"\x01"), 0) == ((1 << 64) - 1, 10)
    with pytest.raises(CodeIndexError):
        decode_index(memoryview(b"\x0a\x00\xc0\x02" + b"\x80" * 9 + b"\x02"))
    with pytest.raises(CodeIndexError):
        read_varint(memoryview(b"\x80" * 9 + b"\x81\x01"), 0)


def test_an_escaped_disambiguator_may_hold_a_closing_parenthesis():
    assert descriptor_name(PKG + "Foo#bar(`x)y`).") == "bar"
    assert descriptor_name(PKG + "Foo#bar().") == "bar"


def test_a_name_with_a_space_is_found_by_name():
    spaced = PKG + "`m`/`operator new`()."
    ix = _index(index(document("a.py", (occurrence([1, 0, 3], spaced, roles=1),))))
    assert [o.symbol for o in ix.definitions("operator new")] == [spaced]
    assert [o.symbol for o in ix.definitions(spaced)] == [spaced]
    assert ix.definitions(PKG + "`m`/missing().") == ()


C_SYM = PKG + "`m`/C#"
B_SYM = PKG + "`m`/B#"


def test_bases_drop_an_ancestor_of_another_listed_base():
    # scip-clang lists every ancestor: C -> B -> A is written as C: [B, A], B: [A]
    ix = _index(index(document("a.py"), external=symbol_info(C_SYM, (B_SYM, BASE_SYM)) + symbol_info(B_SYM, (BASE_SYM,))))
    assert ix.bases(C_SYM) == (B_SYM,)
    assert ix.bases(B_SYM) == (BASE_SYM,)


def test_bases_keep_two_unrelated_bases_and_both_sides_of_a_cycle():
    ix = _index(index(document("a.py"), external=symbol_info(C_SYM, (B_SYM, BASE_SYM))))
    assert ix.bases(C_SYM) == (B_SYM, BASE_SYM)
    cycle = symbol_info(C_SYM, (B_SYM, BASE_SYM)) + symbol_info(B_SYM, (BASE_SYM,)) + symbol_info(BASE_SYM, (B_SYM,))
    assert _index(index(document("a.py"), external=cycle)).bases(C_SYM) == (B_SYM, BASE_SYM)


def test_bases_are_read_as_listed_when_the_index_lists_direct_bases_only():
    # scip-python: D(C), C(B), B(A); a C(B, A) with B(A) keeps A
    d_sym = PKG + "`m`/D#"
    data = (symbol_info(d_sym, (C_SYM,)) + symbol_info(C_SYM, (B_SYM, BASE_SYM)) + symbol_info(B_SYM, (BASE_SYM,))
            + symbol_info(BASE_SYM, (PKG + "`m`/Root#",)) + symbol_info(PKG + "`m`/E#", (d_sym,)))
    ix = _index(index(document("a.py"), external=data))
    assert not ix.lists_ancestors
    assert ix.bases(C_SYM) == (B_SYM, BASE_SYM)


def test_bases_reduce_a_four_level_ancestor_listing():
    d_sym, root = PKG + "`m`/D#", PKG + "`m`/Root#"
    data = (symbol_info(d_sym, (C_SYM, B_SYM, BASE_SYM, root)) + symbol_info(C_SYM, (B_SYM, BASE_SYM, root))
            + symbol_info(B_SYM, (BASE_SYM, root)) + symbol_info(BASE_SYM, (root,)))
    ix = _index(index(document("a.py"), external=data))
    assert ix.lists_ancestors
    assert [ix.bases(s) for s in (d_sym, C_SYM, B_SYM)] == [(C_SYM,), (B_SYM,), (BASE_SYM,)]


def _meta(tool: str) -> bytes:
    return ld(1, ld(2, ld(1, tool.encode()) + ld(2, b"1.0")))


def test_a_known_tool_decides_whether_ancestors_are_listed():
    # C(B, A) with B(A): the only vote is closed, but scip-python lists direct bases
    data = document("a.py"), symbol_info(C_SYM, (B_SYM, BASE_SYM)) + symbol_info(B_SYM, (BASE_SYM,))
    python = _index(index(data[0], external=data[1], meta=_meta("scip-python")))
    assert not python.lists_ancestors and python.bases(C_SYM) == (B_SYM, BASE_SYM)
    clang = _index(index(data[0], external=data[1], meta=_meta("scip-clang")))
    assert clang.lists_ancestors and clang.bases(C_SYM) == (B_SYM,)


def _vote(closed: int, open_: int, method: bool = False) -> bool:
    """lists_ancestors of an unknown tool's index with ``closed`` types listing their grandparent and
    ``open_`` types missing it."""
    data = symbol_info(BASE_SYM, (PKG + "`m`/Root#",))
    for k in range(closed):
        name = PKG + f"`m`/K{k}#" + ("go()." if method else "")
        data += symbol_info(name, (BASE_SYM, PKG + "`m`/Root#"))
    for k in range(open_):
        data += symbol_info(PKG + f"`m`/O{k}#", (BASE_SYM,))
    return _index(index(document("a.py"), external=data, meta=_meta("scip-other"))).lists_ancestors


@pytest.mark.parametrize(("closed", "open_", "expected"), [
    (2, 1, True), (1, 1, False), (1, 2, False), (0, 0, False), (3, 0, True),
])
def test_an_unknown_tool_is_decided_by_a_majority_of_types_with_a_grandparent(closed, open_, expected):
    assert _vote(closed, open_) is expected


def test_a_type_votes_closed_only_when_every_listed_base_has_its_bases_listed():
    root, other, grand = PKG + "`m`/Root#", PKG + "`m`/Other#", PKG + "`m`/Grand#"
    data = (symbol_info(BASE_SYM, (root,)) + symbol_info(other, (grand,))
            + symbol_info(C_SYM, (BASE_SYM, root, other)))
    assert not _index(index(document("a.py"), external=data, meta=_meta("scip-other"))).lists_ancestors


def test_methods_do_not_vote():
    assert _vote(3, 1, method=True) is False


def test_a_type_listing_itself_through_a_cycle_still_votes_closed():
    data = symbol_info(C_SYM, (B_SYM,)) + symbol_info(B_SYM, (C_SYM,))
    assert _index(index(document("a.py"), external=data, meta=_meta("scip-other"))).lists_ancestors


def test_bases_from_two_records_of_one_symbol_are_merged():
    data = index(document("a.py", symbols=symbol_info(C_SYM, (B_SYM,))), external=symbol_info(C_SYM, (BASE_SYM,)))
    assert _index(data).bases(C_SYM) == (B_SYM, BASE_SYM)


def test_an_explicit_zero_is_implementation_is_not_a_base():
    info = ld(3, ld(1, SYM.encode()) + ld(4, ld(1, BASE_SYM.encode()) + vint(3, 0)))
    assert _index(index(document("a.py", symbols=info))).bases(SYM) == ()


@pytest.mark.parametrize(("data", "message"), [
    (b"", "the file is empty"),
    (ld(2, ld(1, b"a.py")), "no metadata message"),
    (b"\x80", "truncated varint"),
    (b"\x80" * 11, "longer than 10 bytes"),
    (b"\x00", "field number 0"),
    (b"\x0a\x00" + key(2**29, 0) + b"\x00", "is above the protobuf maximum"),
    (key(40, 3), "unsupported wire type 3"),
    (key(40, 6), "unsupported wire type 6"),
    (key(40, 5) + b"\x01\x02", "fixed32 field at byte"),
    (key(41, 1) + b"\x01", "fixed64 field at byte"),
    (key(41, 1) + b"\x00" * 7, "fixed64 field at byte"),
    (key(40, 5) + b"\x00" * 3, "fixed32 field at byte"),
    (key(40, 2) + varint(3) + b"ab", "length 3 at byte"),
    (ld(1, key(2, 2) + varint(5) + b"ab"), "runs past the end"),
    (ld(2, ld(2, occurrence([1, 2, 5], SYM))), "a document has no relative_path"),
    (ld(2, vint(1, 5)), "a document has no relative_path"),
    (ld(2, ld(1, b"\xff")), "relative_path is not UTF-8"),
    (ld(2, ld(1, b"a.py") + ld(2, ints(1, [0, 0, 1]) + ld(2, b"\xff"))), "symbol is not UTF-8"),
    (ld(2, ld(1, b"a.py") + ld(2, occurrence([1, 2], SYM))), "range has 2 values"),
    (ld(2, ld(1, b"a.py") + ld(2, occurrence([1, 2, 3, 4, 5], SYM))), "range has 5 values"),
    (ld(2, ld(1, b"a.py") + ld(2, occurrence([1, 2, 5], SYM, enclosing=[1, 2]))), "enclosing range has 2 values"),
    (ld(2, ld(1, b"a.py") + ld(2, key(1, 5) + b"\x00" * 4)), "range has 0 values"),
])
def test_bad_input_raises_code_index_error(data, message):
    with pytest.raises(CodeIndexError, match=message):
        _index(data)


def test_every_truncation_raises_code_index_error_or_reads_a_prefix():
    data = index(
        document("a.py", (occurrence([1, 2, 5], SYM, roles=1, enclosing=[1, 0, 9, 0]),),
                 symbols=symbol_info(SYM, (BASE_SYM,))),
        document("b.py", (occurrence([3, 0, 1], SYM),)),
    )
    whole = _index(data).files()
    raised = read = 0
    for cut in range(len(data)):
        try:
            files = _index(data[:cut]).files()
        except CodeIndexError:
            raised += 1
            continue
        read += 1
        assert files == whole[:len(files)]
    # a cut inside a top-level field must fail; a cut on a field boundary reads the fields before it
    assert raised > 0 and read > 0


def test_random_bytes_raise_only_code_index_error():
    rng = random.Random(20261031)
    head = index(document("a.py", (occurrence([1, 2, 5], SYM),)))
    raised = 0
    for _ in range(500):
        tail = bytes(rng.randrange(256) for _ in range(rng.randrange(1, 40)))
        for data in (tail, head + tail):
            try:
                _index(data)
            except CodeIndexError:
                raised += 1
    # any other exception type escapes and fails the test; garbage must be refused, not read, most of the time
    assert raised > 900


@pytest.mark.parametrize(("symbol", "name"), [
    (PKG + "`a.b`/Foo#", "Foo"),
    (PKG + "`a`/Foo#bar().", "bar"),
    (PKG + "`a`/Foo#bar().(value)", "value"),
    (PKG + "`a`/Foo#[T]", "T"),
    (PKG + "`a`/CONST.", "CONST"),
    (PKG + "`a`/mod/", "mod"),
    (PKG + "`a`/mac!", "mac"),
    (PKG + "`a`/meta:", "meta"),
    (PKG + "`weird name`#", "weird name"),
    (PKG + "`a``b`#", "a`b"),
    ("a  b c 1 2 Foo#", "Foo"),
])
def test_descriptor_name_reads_the_last_defining_descriptor(symbol, name):
    assert descriptor_name(symbol) == name


@pytest.mark.parametrize("symbol", [
    "",
    "local 3",
    "scip-test python pkg",
    "scip-test  python pkg 1.0 Foo#",
    PKG + "Foo",
    PKG + "#",
    PKG + "`a/Foo#",
    PKG + "foo(bar)",
    PKG + "foo(bar",
    PKG + "a/(",
    PKG + "[]",
    PKG + "Foo#[T]x",
])
def test_malformed_or_local_symbols_have_no_name(symbol):
    assert descriptor_name(symbol) is None


def test_a_definition_with_extra_role_bits_is_still_a_definition():
    # roles 9 is Definition | ReadAccess: the definition bit is tested alone, not the whole mask
    ix = _index(index(document("a.py", (occurrence([1, 2, 5], SYM, roles=9, enclosing=[1, 0, 9, 0]),))))
    (occ,) = ix.occurrences("a.py")
    assert occ.is_definition
    assert ix.definitions(SYM) == (occ,)
    assert ix.references(SYM) == ()
    assert ix.enclosing("a.py", 3) is occ


def test_a_typed_multi_line_enclosing_range_keeps_its_end_column():
    # field 11 is a MultiLineRange: its end column (5) must not be lost, or line 6 drops out of the scope
    ix = _index(index(document("a.py", (occurrence([2, 1, 2, 3], SYM, roles=1, extra=typed(11, 2, 1, 6, 5)),))))
    (occ,) = ix.occurrences("a.py")
    assert occ.enclosing_span == (2, 1, 6, 5)
    assert ix.enclosing("a.py", 6) is occ


def test_the_same_symbol_record_in_two_documents_gives_each_base_once():
    # a header seen from two translation units: the same SymbolInformation in both documents
    info = symbol_info(C_SYM, (B_SYM,))
    ix = _index(index(document("a.py", symbols=info), document("b.py", symbols=info)))
    assert ix.bases(C_SYM) == (B_SYM,)
