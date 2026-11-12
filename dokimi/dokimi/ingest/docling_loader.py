# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Optional Docling-based ingestion for non-Markdown documents.

Docling parses PDF, Office (docx/pptx/xlsx), HTML, and scanned/image documents
and exports structured Markdown, letting the benchmark cover corpora beyond the
Markdown-only static loader without flattening structure to plain text.

Docling is an optional dependency (the ``ocr`` extra). Functions that need it
import it lazily and raise a clear, actionable error if it is missing, so the
rest of the pipeline keeps working without it.
"""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path

# Document types Docling can convert to Markdown.
SUPPORTED_SUFFIXES = frozenset({
    ".pdf", ".docx", ".pptx", ".xlsx", ".html", ".htm",
    ".png", ".jpg", ".jpeg", ".tiff", ".tif", ".bmp",
})

_INSTALL_HINT = "Docling is not installed. Install it with: pip install 'dokimi[ocr]'"


def docling_available() -> bool:
    """True if the optional ``docling`` package is importable."""
    return importlib.util.find_spec("docling") is not None


def is_supported(path) -> bool:
    """True if ``path`` has a suffix Docling can ingest."""
    return Path(path).suffix.lower() in SUPPORTED_SUFFIXES


def discover_documents(root, recursive: bool = True) -> list[Path]:
    """Return supported non-Markdown documents under ``root`` (sorted)."""
    root = Path(root)
    if root.is_file():
        return [root] if is_supported(root) else []
    globber = root.rglob if recursive else root.glob
    return sorted(p for p in globber("*") if p.is_file() and is_supported(p))


def convert_to_markdown(path) -> str:
    """Convert a single document to Markdown via Docling.

    Raises ``ImportError`` (with an install hint) if Docling is unavailable.
    """
    try:
        from docling.document_converter import DocumentConverter
    except ImportError as exc:  # pragma: no cover - exercised only without docling
        raise ImportError(_INSTALL_HINT) from exc

    converter = DocumentConverter()
    result = converter.convert(str(path))
    return result.document.export_to_markdown()


def materialize_markdown(inputs, out_dir) -> list[Path]:
    """Convert ``inputs`` to Markdown files under ``out_dir``; return written paths.

    Makes a corpus of PDFs/Office/scans benchmark-ready by emitting one ``.md``
    per source document.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for src in inputs:
        src = Path(src)
        markdown = convert_to_markdown(src)
        # Disambiguate by suffix and a counter so same-named inputs (a.pdf, a.docx,
        # or two a.pdf in different dirs) don't overwrite each other.
        base = f"{src.stem}-{src.suffix.lstrip('.').lower()}"
        dest = out_dir / f"{base}.md"
        i = 2
        while os.path.lexists(dest):  # a link of any kind is taken: a write would follow it (#545)
            dest = out_dir / f"{base}-{i}.md"
            i += 1
        dest.write_text(markdown, encoding="utf-8")
        written.append(dest)
    return written
