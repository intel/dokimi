# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""dataset subcommand group: export artifacts to shareable dataset files."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def cmd_dataset_export(args: argparse.Namespace) -> None:
    """Export a questions/answers/eval/arms artifact to JSONL/Parquet/HF."""
    from dokimi.datasets_export import export_artifact

    try:
        primary = export_artifact(
            kind=args.kind,
            input_path=Path(args.input),
            out_dir=Path(args.out_dir),
            fmt=args.format,
            validate=not args.no_validate,
        )
    except (OSError, ValueError) as exc:  # unreadable, not JSON, invalid, unwritable
        print(f"Error: cannot export {args.input} to {args.out_dir}: {exc}", file=sys.stderr)
        raise SystemExit(2) from None  # 2 as cli.main's other input errors; 1 is a failed gate
    print(f"✅ Exported {args.kind} ({args.format}) → {primary}")
    print(f"   Dataset card: {Path(args.out_dir) / 'README.md'}")


def register(sub, positive_int) -> None:
    dataset_p = sub.add_parser("dataset", help="Export artifacts to shareable datasets")
    dataset_sub = dataset_p.add_subparsers(dest="dataset_cmd", required=True)

    export_p = dataset_sub.add_parser("export", help="Export an artifact to JSONL/Parquet/HF")
    export_p.add_argument("--kind", required=True,
                          choices=["questions", "answers", "eval", "arms", "task_runs"])
    export_p.add_argument("--input", required=True, help="Path to the artifact JSON")
    export_p.add_argument("--out-dir", required=True, help="Output directory")
    export_p.add_argument("--format", default="jsonl", choices=["jsonl", "parquet", "hf"])
    export_p.add_argument("--no-validate", action="store_true",
                          help="Skip schema validation of the input artifact")
    export_p.set_defaults(func=cmd_dataset_export)
