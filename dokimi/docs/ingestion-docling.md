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
dokimi ingest docling --input path/to/docs --out-dir docs/
```

This converts every supported document under `--input` to a `.md` file in
`--out-dir`, ready for `dokimi run`. Supported inputs: `.pdf`, `.docx`,
`.pptx`, `.xlsx`, `.html`/`.htm`, and common image formats (`.png`, `.jpg`,
`.tiff`, …).

If Docling is not installed, the command exits with an install hint and the rest
of the pipeline is unaffected — the dependency is fully optional.

## Programmatic use

```python
from dokimi.ingest.docling_loader import discover_documents, materialize_markdown

docs = discover_documents("raw_corpus/")
materialize_markdown(docs, "docs/")
```
