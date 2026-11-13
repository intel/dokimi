# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Queries that several skills share: a field-wide run on them says nothing about descriptions."""

from __future__ import annotations

# Share of the given queries listed for more than one skill at which a run is warned.
SHARED_QUERY_WARN_SHARE = 0.5


def _key(query: str) -> str:
    return " ".join(query.casefold().split())


def shared_queries_warning(queries_by_skill: dict[str, list[str]]) -> str | None:
    """Warning text when half or more of the queries are shared by several skills, else ``None``.

    A query counts as shared when its text (case and spacing aside) is listed for two or more
    skills. Sibling skills built from one template have identical heading queries, so each
    skill then gets the same rank and the result says nothing about the descriptions.
    """
    owners: dict[str, set[str]] = {}
    for name, queries in queries_by_skill.items():
        for query in queries:
            owners.setdefault(_key(query), set()).add(name)
    total = sum(len(queries) for queries in queries_by_skill.values())
    if len(queries_by_skill) < 2 or not total:
        return None
    shared = sum(1 for queries in queries_by_skill.values() for query in queries
                 if len(owners[_key(query)]) > 1)
    share = shared / total
    if share < SHARED_QUERY_WARN_SHARE:
        return None
    return (f"{shared} of {total} queries ({share:.0%}) are listed for more than one skill, as "
            "when sibling skills built from one template give the same heading queries: every "
            "skill then gets the same rank, and the result says nothing about the descriptions")
