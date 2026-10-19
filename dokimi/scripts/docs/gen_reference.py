# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Generate the CLI and API reference pages at ``mkdocs build`` time.

Run by the ``gen-files`` plugin (mkdocs.yml); nothing is written to the tree.

- ``reference/cli/``: one page per top-level subcommand, the ``--help`` text of
  it and of every nested subcommand, read from ``dokimi.cli.build_parser()`` so
  the page cannot drift from the parser.
- ``reference/api/``: one ``::: module`` page per public module of the
  ``dokimi`` package, rendered by mkdocstrings from the source (no import).
  ``dokimi.commands`` is left out: it is the CLI, documented above. So are
  scripts shipped as package data outside any package (``repos/support/``).
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import mkdocs_gen_files

PROJECT = Path(__file__).resolve().parents[2]
PACKAGE = PROJECT / "dokimi"
sys.path.insert(0, str(PROJECT))  # the package is run from the tree, not installed

from dokimi.cli import build_parser  # noqa: E402


SKIP_API = {("dokimi", "commands")}


def _subcommands(parser: argparse.ArgumentParser) -> dict[str, argparse.ArgumentParser]:
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            subs: dict[str, argparse.ArgumentParser] = {}
            for name, sub in action.choices.items():
                if sub not in subs.values():  # an alias maps to the same parser
                    subs[name] = sub
            return subs
    return {}


def _write_help(fd, parser: argparse.ArgumentParser, level: int) -> None:
    fd.write(f"{'#' * level} `{parser.prog}`\n\n```text\n{parser.format_help()}```\n\n")
    for child in _subcommands(parser).values():
        _write_help(fd, child, min(level + 1, 6))


def cli_reference() -> None:
    os.environ["COLUMNS"] = "100"  # argparse wraps to the terminal; keep pages stable
    os.environ["PYTHON_COLORS"] = "0"  # 3.14+ argparse colours help on a TTY
    root = build_parser()
    nav = mkdocs_gen_files.Nav()
    with mkdocs_gen_files.open("reference/cli/index.md", "w") as fd:
        fd.write("# CLI reference\n\n")
        fd.write("Generated from the argument parser; `dokimi <command> --help` "
                 "(`python cli.py <command> --help` in a source checkout) prints the "
                 "same text.\n\n")
        fd.write(f"```text\n{root.format_help()}```\n")
    nav["dokimi"] = "index.md"
    for name, sub in _subcommands(root).items():
        with mkdocs_gen_files.open(f"reference/cli/{name}.md", "w") as fd:
            _write_help(fd, sub, 1)
        nav[name] = f"{name}.md"
    with mkdocs_gen_files.open("reference/cli/SUMMARY.md", "w") as fd:
        fd.writelines(nav.build_literate_nav())


def api_reference() -> None:
    nav = mkdocs_gen_files.Nav()
    for path in sorted(PACKAGE.rglob("*.py")):
        parts = path.relative_to(PACKAGE.parent).with_suffix("").parts
        doc = Path(*parts).with_suffix(".md")
        if parts[-1] == "__init__":
            # A package's page is its section's index (navigation.indexes).
            parts = parts[:-1]
            doc = doc.with_name("index.md")
        if any(p.startswith("_") for p in parts) or parts[:2] in SKIP_API:
            continue
        if not (path.parent / "__init__.py").exists():
            continue
        nav[parts] = doc.as_posix()
        with mkdocs_gen_files.open(Path("reference/api", doc), "w") as fd:
            fd.write(f"::: {'.'.join(parts)}\n")
        mkdocs_gen_files.set_edit_path(Path("reference/api", doc),
                                       Path("..", path.relative_to(PACKAGE.parent)))
    with mkdocs_gen_files.open("reference/api/SUMMARY.md", "w") as fd:
        fd.writelines(nav.build_literate_nav())


cli_reference()
api_reference()
