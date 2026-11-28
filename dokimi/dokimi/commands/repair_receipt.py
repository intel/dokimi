# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""`catalog repair-receipt BEFORE AFTER EDITED`: what one edit did to the findings of `catalog check` (#960)."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from dokimi.utils import InputError


def add_repair_receipt(catalog_sub) -> None:
    """`catalog repair-receipt BEFORE AFTER EDITED [--out FILE]`: two saved scans, no new scan."""
    parser = catalog_sub.add_parser(
        "repair-receipt",
        help="Compare two saved `catalog check --format json` reports and list the findings one edit cleared, added and left")
    parser.add_argument("before", metavar="BEFORE", help="The report taken before the edit")
    parser.add_argument("after", metavar="AFTER", help="The report taken after the edit")
    parser.add_argument("edited", metavar="EDITED", help="The path of the file the edit changed, recorded in the receipt as given")
    parser.add_argument("--out", default=None, help="Write the receipt as JSON to this file")
    parser.set_defaults(func=cmd_repair_receipt)


def _load(path: str) -> dict:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:  # UnicodeDecodeError and JSONDecodeError are ValueErrors
        raise InputError(f"cannot read {path} as a `catalog check --format json` report (#960, docs/external-content.md, Repair receipt): {exc}") from exc
    if not isinstance(data, dict):
        raise InputError(f"{path} is not a `catalog check --format json` report (#960, docs/external-content.md, Repair receipt)")
    return data


def cmd_repair_receipt(args: argparse.Namespace) -> None:
    """Print the receipt as JSON and, with ``--out``, save it."""
    from dokimi.external.repair_receipt import repair_receipt

    try:
        receipt = repair_receipt(_load(args.before), _load(args.after), args.edited)
    except ValueError as exc:  # a report without `skills`; docs/external-content.md, Repair receipt (#960)
        raise InputError(str(exc)) from exc
    text = json.dumps(receipt, indent=2)
    if args.out:
        out = Path(args.out)
        try:
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(text, encoding="utf-8")
        except OSError as exc:
            raise InputError(f"cannot write --out {out} (#960): {exc}") from exc
    print(text)
