# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The key-free catalog consumer path: static plan, checks and honest receipts."""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import platform
import shutil
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from dokimi import __version__
from dokimi.artifacts import save_artifact
from dokimi.exit_codes import usage_exit

_OUTPUTS = ("catalog.json", "plan.json", "result.json", "report.md")
_REPORT_HEADER = "# Catalog static evaluation"
_RESULT_DETAIL = "Deterministic checks only"


def _is_receipt(path: Path) -> bool:
    """A regular file this command wrote: its report header or catalog-static payload."""
    if path.name not in _OUTPUTS or path.is_symlink() or not path.is_file():
        return False
    try:
        text = path.read_text(encoding="utf-8")
        if path.name == "report.md":
            return text.startswith(_REPORT_HEADER)
        payload = json.loads(text)
        if path.name == "plan.json":
            return payload["harnesses"] == ["catalog-static"]
        if path.name == "result.json":
            cells = payload["cells"]
            return isinstance(cells, list) and bool(cells) and all(
                str(c["detail"]).startswith(_RESULT_DETAIL) for c in cells)
        return str(payload["schema_version"]).startswith("catalog_eval.")
    except (OSError, ValueError, KeyError, TypeError, AttributeError, RecursionError):
        return False


def _output(root: Path, out: Path) -> None:
    """Require a dedicated output directory outside all catalog input trees."""
    if root.is_relative_to(out) or out.is_relative_to(root):
        raise ValueError("--out-dir must be outside the entire catalog checkout and its ancestors")
    if out.exists() and (not out.is_dir() or any(not _is_receipt(p) for p in out.iterdir())):
        raise ValueError("--out-dir must be an empty directory or contain only earlier catalog evaluation receipts")


def _resolve(path: Path) -> Path:
    try:
        return path.resolve()
    except (OSError, RuntimeError) as exc:
        raise ValueError(f"cannot resolve {path}: {exc}") from exc


def _write(out: Path, report: dict[str, Any], plan: dict[str, Any], result: dict[str, Any]) -> None:
    """Prepare all schema-checked files before replacing a previous output directory."""
    from dokimi.external.catalog_receipts import render

    out.parent.mkdir(parents=True, exist_ok=True)
    with _workdir(out.parent) as work:
        staged = Path(work) / "new"
        staged.mkdir()
        for kind, payload in (("catalog_eval", report), ("plan", plan), ("result", result)):
            name = "catalog.json" if kind == "catalog_eval" else f"{kind}.json"
            save_artifact(kind, payload, staged / name)
        (staged / "report.md").write_text(render(report), encoding="utf-8", errors="backslashreplace")
        _replace_output(out, staged, Path(work) / "old")


class _RollbackFailed(OSError):
    """The previous output could not be put back; its copy is kept for recovery."""


@contextlib.contextmanager
def _workdir(parent: Path) -> Iterator[Path]:
    """A scratch directory, kept only while it still holds the sole copy of the previous output."""
    work = Path(tempfile.mkdtemp(prefix=".catalog-eval-", dir=parent))
    try:
        yield work
    finally:
        if not (work / "old").exists():
            shutil.rmtree(work, ignore_errors=True)


def _replace_output(out: Path, staged: Path, backup: Path) -> None:
    try:
        if out.exists():
            os.replace(out, backup)
        os.replace(staged, out)
    except BaseException as exc:
        if backup.exists():
            try:
                os.replace(backup, out)
            except OSError as restore:
                raise _RollbackFailed(f"cannot restore previous receipts, kept at {backup}: {restore}") from exc
        raise
    shutil.rmtree(backup, ignore_errors=True)


def _write_error(out: Path, error: Exception) -> None:
    """Invalidate earlier success after an input error without inventing a measured plan."""
    out.parent.mkdir(parents=True, exist_ok=True)
    with _workdir(out.parent) as work:
        staged = Path(work) / "new"
        staged.mkdir()
        message = str(error).replace("<", "&lt;").replace(">", "&gt;")
        (staged / "report.md").write_text(
            f"# Catalog static evaluation\n\nInput error: {message}\n\n"
            "No valid result was produced. Earlier success receipts were removed.\n",
            encoding="utf-8", errors="backslashreplace")
        _replace_output(out, staged, Path(work) / "old")


def _checker() -> dict[str, str]:
    """Bind the installed checker code/schemas independently of its package version."""
    package = Path(__file__).resolve().parents[1]
    digest = hashlib.sha256()
    for path in sorted(package.rglob("*")):
        if path.is_file() and (path.suffix == ".py" or path.parent.name == "schemas"):
            name = path.relative_to(package).as_posix().encode("utf-8")
            content = path.read_bytes()
            digest.update(len(name).to_bytes(8, "big") + name)
            digest.update(len(content).to_bytes(8, "big") + content)
    return {"dokimi_version": __version__, "python_version": platform.python_version(),
            "package_sha256": "sha256:" + digest.hexdigest()}


def cmd_catalog_eval(args: argparse.Namespace) -> None:
    from dokimi.external.catalog_eval import evaluate_catalog
    from dokimi.external.catalog_git import source_and_changes
    from dokimi.external.catalog_receipts import receipts
    from dokimi.external.catalog_eval_inputs import checked_root

    supplied_root = Path(args.catalog_root).absolute()
    out = Path(args.out_dir).absolute()
    output_valid = False
    try:
        root, out = _resolve(supplied_root), _resolve(out)
        _output(root, out)
        output_valid = True
        root = checked_root(supplied_root)
        source, changed, note = source_and_changes(root, args.changed_from, out)
        report = evaluate_catalog(root, changed_paths=changed)
        report["source"] = source
        report["checker"] = _checker()
        if note:
            report["selection_note"] = note
        plan, result = receipts(report)
        report["plan_digest"] = result["plan_digest"]
        _write(out, report, plan, result)
    except (OSError, ValueError) as exc:
        if output_valid:
            try:
                _write_error(out, exc)
            except OSError as write_exc:
                usage_exit(f"catalog eval: {exc}; cannot invalidate earlier receipts: {write_exc}")
        usage_exit(f"catalog eval: {exc}")
    print(f"Static catalog checks: {report['status']}; receipts: {out}")
    if report["status"] != "pass":
        raise SystemExit(1)


def register(sub: Any) -> None:
    parser = sub.add_parser("eval", help="Plan and check a catalog without running models, Docker or skill scripts")
    parser.add_argument("catalog_root", type=Path, help="Local catalog checkout")
    parser.add_argument("--tier", choices=["static"], default="static", help="static: deterministic checks only")
    parser.add_argument("--out-dir", type=Path, required=True, help="Dedicated directory for plan, result and report")
    parser.add_argument("--changed-from", help="Local base commit; an unavailable base conservatively checks every skill")
    parser.set_defaults(func=cmd_catalog_eval)
