# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""data: schemas, dataset export and imports of other formats (#94 M2).

``data schema show KIND`` prints one schema as JSON: ``cli_routes`` (the route table) and
``cli_output.v1`` (the document every ``--json`` leaf prints).
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable

from dokimi import cli_output, cli_routes

SCHEMA_KINDS: dict[str, Callable[[], dict]] = {
    "cli_routes": cli_routes.document,
    "cli_output.v1": cli_output.schema_document,
}


def cmd_schema_list(args: argparse.Namespace) -> None:
    """Print the schema kinds, one per line."""
    for kind in SCHEMA_KINDS:
        print(kind)


def cmd_schema_show(args: argparse.Namespace) -> None:
    """Print one schema kind as JSON."""
    print(json.dumps(SCHEMA_KINDS[args.kind](), indent=2))


def register(sub, positive_int):  # noqa: ARG001 - the registry passes it to every command
    data_p = sub.add_parser("data", help="Schemas, dataset export, and imports of other formats")
    data_sub = data_p.add_subparsers(dest="data_cmd", required=True)

    schema_p = data_sub.add_parser("schema", help="List schema kinds and print one as JSON (the CLI route table, the --json document)")
    schema_sub = schema_p.add_subparsers(dest="schema_cmd", required=True)

    list_p = schema_sub.add_parser("list", help="List the schema kinds")
    list_p.set_defaults(func=cmd_schema_list)

    show_p = schema_sub.add_parser("show", help="Print one schema kind as JSON")
    show_p.add_argument("kind", choices=sorted(SCHEMA_KINDS))
    show_p.set_defaults(func=cmd_schema_show)

    from dokimi.commands import dataset, ingest  # the leaves moved here in #94 M2

    dataset.add_export(data_sub)
    import_p = data_sub.add_parser("import", help="Import other formats as Markdown")
    import_sub = import_p.add_subparsers(dest="data_import_cmd", required=True)
    ingest.add_docling(import_sub)
