#!/usr/bin/env python3
"""docs-new-page: the page exists, is in the published site's nav, and is in the docs index.

cwd is <workspace>/dokimi. Prints one JSON object of metrics as its last stdout line.
"""
import json
import re
import sys
from pathlib import Path

sys.stderr = sys.stdout
PAGE = "repo-scan-quickstart.md"
m = {}
page = Path("docs") / PAGE
try:
    text = page.read_text(encoding="utf-8") if page.exists() else ""
    m["page_exists"] = len(text.strip()) > 200
    m["page_runs_repo_scan"] = bool(re.search(r"repo scan\b", text))
    m["page_shows_manifest"] = "repo_manifest.v1" in text
    nav = Path("mkdocs.yml").read_text(encoding="utf-8")
    nav_part = nav[nav.find("\nnav:"):] if "\nnav:" in nav else ""
    m["in_mkdocs_nav"] = PAGE in nav_part
    index = Path("docs/README.md").read_text(encoding="utf-8")
    m["in_docs_index"] = bool(re.search(r"\]\(\.?/?" + re.escape(PAGE) + r"\)", index))
except Exception as exc:  # a broken workspace fails the checks, it does not crash the grader
    print(f"check error: {exc}")
    for k in ("page_exists", "page_runs_repo_scan", "page_shows_manifest", "in_mkdocs_nav", "in_docs_index"):
        m.setdefault(k, False)
print(json.dumps(m))
