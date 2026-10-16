# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Schema-checked fixtures for artifact readers' tests.

A hand-built artifact fixture is a claim about what a producer writes. A reader tested against a
fixture the producer would never write passes its tests and fails on real runs: the panel judge
read ``question``/``text`` while the answerer writes ``question_text``, and its fixture used the
reader's guess. :func:`artifact_fixture` holds a fixture to the schema ``save_artifact`` holds the
producer to, plus one closed-world step the open schemas do not take: a key the schema does not
declare, at a level where it declares any, is rejected. The schema is where a producer documents
its record keys, so a fixture can only use keys some producer is documented to write.
"""

from __future__ import annotations

import json
from typing import Any

from dokimi.artifacts import SCHEMA_DIR, SCHEMA_VERSIONS, validate_artifact


def undeclared_keys(schema: Any, instance: Any, path: str = "", root: Any = None) -> list[str]:
    """Keys of ``instance`` the schema does not declare, wherever it declares ``properties``.

    An object with an explicit ``additionalProperties`` is open by declaration and not checked;
    one without it is open in the schema (writers may add keys) but closed for a fixture. Walks
    ``properties``, array ``items`` and local ``$ref``; ``allOf``/``anyOf`` branches are not
    walked, so a key declared only there is not reachable.
    """
    root = schema if root is None else root
    if not isinstance(schema, dict):
        return []
    ref = schema.get("$ref")
    if isinstance(ref, str) and ref.startswith("#/"):
        target: Any = root
        for part in ref[2:].split("/"):
            target = target[part]
        return undeclared_keys(target, instance, path, root)
    found: list[str] = []
    props = schema.get("properties")
    if isinstance(props, dict) and isinstance(instance, dict):
        for key, value in instance.items():
            where = f"{path}/{key}" if path else key
            if key in props:
                found += undeclared_keys(props[key], value, where, root)
            elif "additionalProperties" not in schema:
                found.append(f"{where}: not declared (declared: {', '.join(sorted(props))})")
    items = schema.get("items")
    if isinstance(items, dict) and isinstance(instance, list):
        for i, value in enumerate(instance):
            found += undeclared_keys(items, value, f"{path}/{i}" if path else str(i), root)
    return found


def artifact_fixture(kind: str, payload: dict, *, closed: bool = True) -> dict:
    """``payload`` stamped as a current ``kind`` artifact, after checking it is one.

    Raises ``AssertionError`` naming every problem: a schema violation (what ``save_artifact``
    would refuse to write) or, with ``closed``, an undeclared key (what no documented producer
    writes). Returns a new dict; ``payload`` is not mutated.
    """
    fixture = {"schema_version": SCHEMA_VERSIONS[kind],
               **{k: v for k, v in payload.items() if k != "schema_version"}}
    validate_artifact(kind, fixture)
    if closed:
        schema = json.loads((SCHEMA_DIR / f"{SCHEMA_VERSIONS[kind]}.json").read_text("utf-8"))
        problems = undeclared_keys(schema, fixture)
        assert not problems, f"{kind} fixture uses keys no producer declares:\n  " + "\n  ".join(problems)
    return fixture
