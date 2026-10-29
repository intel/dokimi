# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""data: schemas of the artifacts and of the CLI routes. ``data schema show KIND`` prints one as JSON."""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable

from dokimi import cli_routes

SCHEMA_KINDS: dict[str, Callable[[], dict]] = {
    "cli_routes": cli_routes.document,
}


def cmd_schema_list(args: argparse.Namespace) -> None:
    """Print the schema kinds, one per line."""
    for kind in SCHEMA_KINDS:
        print(kind)


def cmd_schema_show(args: argparse.Namespace) -> None:
    """Print one schema kind as JSON."""
    print(json.dumps(SCHEMA_KINDS[args.kind](), indent=2))


def register(sub, positive_int):  # noqa: ARG001 - the registry passes it to every command
    data_p = sub.add_parser("data", help="Schemas of the artifacts and the CLI routes")
    data_sub = data_p.add_subparsers(dest="data_cmd", required=True)

    schema_p = data_sub.add_parser("schema", help="Print an artifact or route schema")
    schema_sub = schema_p.add_subparsers(dest="schema_cmd", required=True)

    list_p = schema_sub.add_parser("list", help="List the schema kinds")
    list_p.set_defaults(func=cmd_schema_list)

    show_p = schema_sub.add_parser("show", help="Print one schema kind as JSON")
    show_p.add_argument("kind", choices=sorted(SCHEMA_KINDS))
    show_p.set_defaults(func=cmd_schema_show)
