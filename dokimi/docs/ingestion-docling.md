---
depends_on:
  - dokimi/dokimi/ingest/docling_loader.py
  - dokimi/dokimi/commands/ingest.py
---
<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

# Ingesting non-Markdown documents (Docling)

The static benchmark loader reads Markdown only. To benchmark PDF, Office
(`.docx`/`.pptx`/`.xlsx`), HTML, or scanned/image documents, convert them to
Markdown first with [Docling](https://github.com/docling-project/docling), which
preserves document structure rather than flattening to plain text.

Docling is an optional dependency:

```bash
pip install 'dokimi[ocr]'
```

## Materialize a corpus

```bash
dokimi data import docling --input path/to/docs --out-dir docs/
```

This converts every supported document under `--input` to a `.md` file in
`--out-dir`, ready for `dokimi docs check`. Supported inputs: `.pdf`, `.docx`,
`.pptx`, `.xlsx`, `.html`/`.htm`, and common image formats (`.png`, `.jpg`,
`.tiff`, …).

Each source becomes `<stem>-<ext>.md` (`manual.pdf` → `manual-pdf.md`). The
output directory keeps `.dokimi-docling.json`, a `docling_sources.v1` record of
which source each file came from, so a rerun replaces each source's own file.
A second source with the same name, such as `v2/manual.pdf`, gets the next
numbered name, `manual-pdf-2.md`, in the same run or a later one. A file the
record does not name, a directory and a symbolic link are never written over:
the source gets the next free name instead. A deleted file's name stays with its
source while that source exists, and names that differ only in letter case count as one. A directory at
the record's own name is refused with a message naming it. If the record cannot be read, a
warning names it, the files stay, and the run writes new numbered ones beside
them.

If Docling is not installed, the command exits with an install hint and the rest
of the pipeline is unaffected — the dependency is fully optional.

## Programmatic use

```python
from dokimi.ingest.docling_loader import discover_documents, materialize_markdown

docs = discover_documents("raw_corpus/")
materialize_markdown(docs, "docs/")
```
