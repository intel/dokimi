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
import itertools
import logging
import os
from pathlib import Path

from dokimi.artifacts import load_artifact, save_artifact
from dokimi.atomic_write import write_text_atomic
from dokimi.utils import InputError

logger = logging.getLogger(__name__)

# Document types Docling can convert to Markdown.
SUPPORTED_SUFFIXES = frozenset({
    ".pdf", ".docx", ".pptx", ".xlsx", ".html", ".htm",
    ".png", ".jpg", ".jpeg", ".tiff", ".tif", ".bmp",
})

_INSTALL_HINT = "Docling is not installed. Install it with: pip install 'dokimi[ocr]'"

# In the output directory: which source each .md was converted from (schemas/docling_sources.v1.json).
SOURCES_NAME = ".dokimi-docling.json"


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

    Makes a corpus of PDFs/Office/scans benchmark-ready by emitting one ``<stem>-<ext>.md`` per
    source document. A source converted into ``out_dir`` before replaces its own file (#687): the
    ``.dokimi-docling.json`` record there names each file's source. Another source with the same
    name, a file no record names and a link of any kind are left alone, and the next free
    ``<stem>-<ext>-N.md`` is written instead.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    record = out_dir / SOURCES_NAME
    owners = _read_owners(record)
    written: list[Path] = []
    for src in inputs:
        src = Path(src)
        markdown = convert_to_markdown(src)
        source = _source_id(src, out_dir)
        dest = _dest(src, out_dir, owners, source)
        # Recorded before the write: a file is never left that no record names (a rerun would write a -2).
        owners = {name: s for name, s in owners.items() if s != source} | {dest.name: source}
        _save_owners(record, owners)
        write_text_atomic(dest, markdown)
        written.append(dest)
    return written


def _source_id(src: Path, out_dir: Path) -> str:
    """The source as the record names it: relative to ``out_dir``, so the two can move together.
    Links are not followed: a link is a source of its own, whatever it points at."""
    absolute = os.path.abspath(src)
    try:
        return Path(os.path.relpath(absolute, os.path.abspath(out_dir))).as_posix()
    except ValueError:  # on another Windows drive there is no relative path
        return Path(absolute).as_posix()


def _dest(src: Path, out_dir: Path, owners: dict[str, str], source: str) -> Path:
    """The file the record gives ``source``, else the first free name: a link of any kind is taken (#545)."""
    for name, owner in owners.items():
        path = out_dir / name
        if owner != source or path.name != name:  # a `\` is a separator on Windows: not a name there
            continue
        if path.is_file() and not path.is_symlink() or not os.path.lexists(path):
            return path
    base = f"{src.stem}-{src.suffix.lstrip('.').lower()}"
    names = itertools.chain([f"{base}.md"], (f"{base}-{i}.md" for i in itertools.count(2)))
    return next(out_dir / n for n in names if not os.path.lexists(out_dir / n))


def _read_owners(record: Path) -> dict[str, str]:
    """File name -> source, from the record an earlier run left; empty when there is none to use."""
    if not os.path.lexists(record):
        return {}
    if record.is_symlink():
        reason = f"{record} is a link"
    else:
        try:
            return {f["name"]: f["source"] for f in load_artifact("docling_sources", record)["files"]}
        except InputError as exc:
            reason = str(exc)
    # the files stay: a rerun writes beside them and starts a new record
    logger.warning("Ignoring the record of converted files: %s. The files already in %s are kept, "
                   "and this run writes new ones beside them", reason, record.parent)
    return {}


def _save_owners(record: Path, owners: dict[str, str]) -> None:
    if record.is_symlink():  # save_artifact would write through it, out of out_dir (#545)
        record.unlink()
    files = [{"name": name, "source": source} for name, source in sorted(owners.items())]
    save_artifact("docling_sources", {"files": files}, record)
