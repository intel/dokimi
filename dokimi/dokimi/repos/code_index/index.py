# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""A SCIP code index read from one file: definitions, references, enclosing scopes and base classes.

Lines are 0-based, as in SCIP. Paths are repo-relative once ``root_prefix`` is applied. A ``local N`` id is
scoped to one document, so the queries that take a symbol refuse it: read it with :meth:`CodeIndex.occurrences`.
"""

from __future__ import annotations

from pathlib import Path

from dokimi.repos.code_index.scip import Decoded, Occurrence, decode_index
from dokimi.repos.code_index.symbols import descriptor_name, is_local


# Whether a tool's is_implementation relationships list every ancestor (checked on scip-clang 0.4.0 and
# scip-python 0.6.8); another tool is decided by CodeIndex.lists_ancestors' vote.
_TOOLS_LISTING_ANCESTORS = {"scip-clang": True, "scip-python": False}


class CodeIndex:
    """The occurrences, definitions and base-class relations of one SCIP index."""

    def __init__(self, decoded: Decoded) -> None:
        self._tool = decoded.tool
        self._docs = {path: tuple(occs) for path, occs in sorted(decoded.occurrences.items())}
        self._by_symbol: dict[str, list[Occurrence]] = {}
        self._by_name: dict[str, list[Occurrence]] = {}
        self._scopes: dict[str, list[tuple[int, int, Occurrence]]] = {}
        names: dict[str, str | None] = {}
        for occs in decoded.occurrences.values():
            for occ in occs:
                if not occ.symbol:
                    continue
                # a local id is scoped to its document, so its scope is keyed by that document: keep it
                if occ.is_definition and occ.enclosing is not None:
                    first, last = occ.enclosing
                    self._scopes.setdefault(occ.path, []).append((first, last, occ))
                if is_local(occ.symbol):
                    continue
                self._by_symbol.setdefault(occ.symbol, []).append(occ)
                if not occ.is_definition:
                    continue
                if occ.symbol not in names:
                    names[occ.symbol] = descriptor_name(occ.symbol)
                name = names[occ.symbol]
                if name is not None:
                    self._by_name.setdefault(name, []).append(occ)
        self._bases = decoded.bases
        self._lists_ancestors: bool | None = None

    @property
    def tool(self) -> str:
        """The tool that wrote the index, as ``name version`` (empty when the metadata has none)."""
        return self._tool

    @property
    def has_enclosing_ranges(self) -> bool:
        """True when some definition carries an enclosing range (scip-clang 0.4 writes none)."""
        return bool(self._scopes)

    def files(self) -> tuple[str, ...]:
        """The repo-relative paths of the indexed documents, sorted. A file the index does not list is not covered."""
        return tuple(self._docs)

    def occurrences(self, path: str) -> tuple[Occurrence, ...]:
        """Every occurrence in one document, in the order the index holds them, an identical repeat kept once."""
        return self._docs.get(path, ())

    def definitions(self, query: str) -> tuple[Occurrence, ...]:
        """The definition occurrences of a symbol, or of a name.

        A query that is a global symbol of this index is looked up exactly. Any other query is a name, matched
        against the last descriptor of each symbol: namespace, type, term, method, meta, macro, parameter or
        type parameter (``Impl#run().`` is ``run``, ``Impl#run().(value)`` is ``value``). A name may hold
        spaces (``operator new``); a qualified path such as ``pkg.module.run`` matches nothing."""
        _refuse_local(query)
        if query in self._by_symbol:
            return tuple(occ for occ in self._by_symbol[query] if occ.is_definition)
        return tuple(self._by_name.get(query, ()))

    def references(self, symbol: str) -> tuple[Occurrence, ...]:
        """Every non-definition occurrence of a global symbol. Takes the full symbol string, not a name."""
        _refuse_local(symbol)
        return tuple(occ for occ in self._by_symbol.get(symbol, ()) if not occ.is_definition)

    def enclosing(self, path: str, line: int) -> Occurrence | None:
        """The innermost definition whose enclosing range holds ``line`` of ``path``, or ``None``.

        A definition with no enclosing range never matches, so this is ``None`` on an index without ranges."""
        best: Occurrence | None = None
        best_span = -1
        for first, last, occ in self._scopes.get(path, ()):
            if first <= line <= last and (best is None or last - first < best_span):
                best, best_span = occ, last - first
        return best

    def bases(self, symbol: str) -> tuple[str, ...]:
        """The symbols ``symbol`` directly implements or extends.

        Read from SCIP ``is_implementation`` relationships. scip-python lists direct bases only; scip-clang lists
        every ancestor. On an index that lists ancestors (:attr:`lists_ancestors`), a listed symbol that is a base
        of another listed one is dropped (both stay when each lists the other)."""
        _refuse_local(symbol)
        listed = self._bases.get(symbol, ())
        if len(listed) < 2 or not self.lists_ancestors:
            return listed
        above = {base: set(self._bases.get(base, ())) for base in listed}
        return tuple(base for base in listed
                     if not any(base in above[other] and other not in above[base] for other in listed))

    @property
    def lists_ancestors(self) -> bool:
        """True when the index lists every ancestor of a class, not only its direct bases.

        Known tools decide it: scip-clang lists ancestors, scip-python does not. For another tool it is a vote
        over the types (``#`` symbols) that have a grandparent: True when more than half of them list every base
        of each listed base too. With no grandparent anywhere there is nothing to drop, so the vote is False."""
        if self._lists_ancestors is None:
            self._lists_ancestors = _TOOLS_LISTING_ANCESTORS.get(self._tool.split(" ")[0])
        if self._lists_ancestors is None:
            closed = deep = 0
            for symbol, listed in self._bases.items():
                above = [self._bases.get(base, ()) for base in listed]
                if not symbol.endswith("#") or not any(above):
                    continue
                deep += 1
                closed += all(set(grand) <= set(listed) | {symbol} for grand in above)
            self._lists_ancestors = 2 * closed > deep
        return self._lists_ancestors


def load(path: str | Path, *, root_prefix: str = "") -> CodeIndex:
    """Read the SCIP index at ``path``. ``root_prefix`` is put before each document path, so an index written
    relative to a subdirectory (dokimi's own index is relative to ``dokimi/``) maps to repo paths.

    Raises :class:`~dokimi.repos.errors.CodeIndexError` for an empty, truncated or garbled file. A file cut at a
    document boundary still decodes, with fewer documents: compare :meth:`CodeIndex.files` with the tree."""
    return CodeIndex(decode_index(memoryview(Path(path).read_bytes()), root_prefix))


def _refuse_local(symbol: str) -> None:
    if is_local(symbol):
        raise ValueError(f"{symbol!r} is a local id, scoped to one document: read it with occurrences(path)")
