#!/usr/bin/env python3
"""Checks for rename-cli-flag (`benchmark run --multi-run` -> `--repeats`).

cwd = <workspace>/dokimi; argv: <run_dir> <task_dir>. Prints one JSON object of metrics
as the last stdout line.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import re
import sys
import tempfile
import traceback
from pathlib import Path

sys.path.insert(0, os.getcwd())  # the workspace's package, not an installed one
# run.py reads the last line of stdout+stderr: nothing may follow the JSON on stderr
sys.stderr = sys.stdout

PROJECT = Path.cwd()
ROOT = PROJECT.parent
OLD = re.compile(r"--multi-run(?![\w-])")
NEW = re.compile(r"--repeats(?![\w-])")

metrics: dict = {}
notes: list = []


def guard(name):
    def deco(fn):
        try:
            metrics[name] = bool(fn())
        except (Exception, SystemExit):
            metrics[name] = False
            notes.append(f"{name}: {traceback.format_exc(limit=4)}")
        return fn
    return deco


def run_benchmark(extra: list[str]) -> list[dict]:
    """Parse `benchmark run` with ``extra`` and run it with the pipeline stubbed out."""
    from dokimi.cli import build_parser
    from dokimi.commands import benchmark
    from dokimi.registry import ProductEntry

    class Registry:
        def get(self, key):
            assert key == "onetbb", key
            return ProductEntry(key="onetbb", name="oneTBB", description="x", doc_sources=["local:docs"])

    calls: list[dict] = []

    def fake_single(entry, **kwargs):
        calls.append(kwargs)
        return {"library": entry.key, "name": entry.name, "status": "ok",
                "result": {"steps": {"evaluation": {"summary": {"with_avg": 80.0}}}}}

    saved = benchmark._load_registry, benchmark._run_single_library
    benchmark._load_registry = lambda args: Registry()
    benchmark._run_single_library = fake_single
    try:
        with tempfile.TemporaryDirectory() as tmp:
            out = str(Path(tmp) / "onetbb_eval")
            args = build_parser().parse_args(["benchmark", "run", "--target", "onetbb", "--output-dir", out, *extra])
            with contextlib.redirect_stdout(io.StringIO()):
                args.func(args)
            for c in calls:
                c["output_dir"] = str(c.get("output_dir", "")).replace(out, "OUT")
    finally:
        benchmark._load_registry, benchmark._run_single_library = saved
    return calls


@guard("new_flag_ok")
def _():
    import dokimi
    assert Path(dokimi.__file__).resolve().is_relative_to(PROJECT.resolve()), dokimi.__file__
    three = run_benchmark(["--repeats", "3"])
    assert [c["output_dir"] for c in three] == ["OUT_run1", "OUT_run2", "OUT_run3"], three
    one = run_benchmark([])
    assert [c["output_dir"] for c in one] == ["OUT"], one
    return True


@guard("old_flag_gone")
def _():
    from dokimi.cli import build_parser
    try:
        with contextlib.redirect_stderr(io.StringIO()):
            build_parser().parse_args(["benchmark", "run", "--target", "onetbb", "--multi-run", "3"])
    except SystemExit as exc:
        return exc.code != 0
    return False


def doc_files() -> list[Path]:
    files = sorted((PROJECT / "docs").rglob("*.md"))
    files += [PROJECT / "README.md", ROOT / "README.md", ROOT / "CONTRIBUTING.md", PROJECT / "CONTRIBUTING.md"]
    return [f for f in files if f.is_file()]


@guard("docs_updated")
def _():
    stale = [str(f.relative_to(ROOT)) for f in doc_files() if OLD.search(f.read_text(encoding="utf-8"))]
    if stale:
        notes.append(f"docs_updated: still name --multi-run: {stale}")
    return not stale


@guard("docs_name_new")
def _():
    # `tasks run --repeats` already exists: look for a `benchmark run` command line that passes it
    rx = re.compile(r"benchmark\s+run\b[^\n]*--repeats(?![\w-])")
    return any(rx.search(re.sub(r"\s*\\\n\s*", " ", f.read_text(encoding="utf-8"))) for f in doc_files())


@guard("tests_mention_new")
def _():
    return any(NEW.search(t) and "benchmark" in t
               for t in (f.read_text(encoding="utf-8", errors="replace") for f in (PROJECT / "tests").rglob("*.py")))


@guard("code_free_of_old")
def _():
    return not any(OLD.search(f.read_text(encoding="utf-8", errors="replace"))
                   for f in (PROJECT / "dokimi").rglob("*.py"))


for n in notes:
    print(n)
print(json.dumps(metrics))
