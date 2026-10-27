# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""ingest subcommand group: materialize Markdown from other document formats."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def cmd_ingest_docling(args: argparse.Namespace) -> None:
    """Convert PDF/Office/HTML/scans under --input to benchmark-ready Markdown."""
    from dokimi.ingest.docling_loader import (
        discover_documents,
        docling_available,
        materialize_markdown,
    )

    if not docling_available():
        print(
            "Docling is not installed. Install it with: pip install 'dokimi[ocr]'",
            file=sys.stderr,
        )
        sys.exit(2)

    docs = discover_documents(Path(args.input), recursive=not args.no_recursive)
    if not docs:
        print(f"No supported documents found under {args.input}", file=sys.stderr)
        sys.exit(1)

    print(f"Converting {len(docs)} document(s) → {args.out_dir}")
    try:
        written = materialize_markdown(docs, Path(args.out_dir))
    except OSError as exc:  # --out-dir is a file or unwritable, or a source cannot be read
        print(f"Error: ingest into {args.out_dir} failed: {exc}", file=sys.stderr)
        sys.exit(2)  # 2 as cli.main's other input errors; 1 is "nothing to convert"
    for path in written:
        print(f"  ✓ {path}")
    print(f"✅ Materialized {len(written)} Markdown file(s)")


def register(sub, positive_int) -> None:
    ingest_p = sub.add_parser("ingest", help="Materialize Markdown from other formats")
    ingest_sub = ingest_p.add_subparsers(dest="ingest_cmd", required=True)

    docling_p = ingest_sub.add_parser(
        "docling", help="Convert PDF/Office/HTML/scans to Markdown via Docling (ocr extra)"
    )
    docling_p.add_argument("--input", required=True, help="Document file or directory")
    docling_p.add_argument("--out-dir", required=True, help="Output directory for .md files")
    docling_p.add_argument("--no-recursive", action="store_true",
                           help="Do not recurse into subdirectories")
    docling_p.set_defaults(func=cmd_ingest_docling)
