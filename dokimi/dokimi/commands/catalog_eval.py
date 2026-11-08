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
from dokimi.utils import InputError

_OUTPUTS = ("catalog.json", "plan.json", "result.json", "report.md")
_REPORT_HEADER = "# Catalog static evaluation"
_SCRATCH = ".catalog-eval-"
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


def _is_scratch(path: Path) -> bool:
    """A scratch directory an interrupted run of this command left in the output directory."""
    return path.name.startswith(_SCRATCH) and path.is_dir() and not path.is_symlink()


def _output(root: Path, out: Path) -> None:
    """Require a dedicated output directory outside all catalog input trees."""
    if root.is_relative_to(out) or out.is_relative_to(root):
        raise ValueError("--out-dir must be outside the entire catalog checkout and its ancestors")
    if out.exists() and (not out.is_dir() or any(not _is_receipt(p) and not _is_scratch(p) for p in out.iterdir())):
        raise ValueError("--out-dir must be an empty directory or contain only earlier catalog evaluation receipts")


def _resolve(path: Path) -> Path:
    try:
        return path.resolve()
    except (OSError, RuntimeError) as exc:
        raise ValueError(f"cannot resolve {path}: {exc}") from exc


def _write(out: Path, report: dict[str, Any], plan: dict[str, Any], result: dict[str, Any]) -> None:
    """Prepare all schema-checked files before replacing the previous receipts."""
    from dokimi.external.catalog_receipts import render

    with _workdir(out) as work:
        staged = Path(work) / "new"
        staged.mkdir()
        for kind, payload in (("catalog_eval", report), ("plan", plan), ("result", result)):
            name = "catalog.json" if kind == "catalog_eval" else f"{kind}.json"
            save_artifact(kind, payload, staged / name)
        (staged / "report.md").write_text(render(report), encoding="utf-8", errors="backslashreplace")
        _replace_receipts(out, staged, Path(work) / "old")


class _RollbackFailed(OSError):
    """The previous receipts could not be put back; their copy is kept for recovery."""


@contextlib.contextmanager
def _workdir(out: Path) -> Iterator[Path]:
    """A scratch directory inside *out*, kept only while it holds the sole copy of the previous receipts."""
    out.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix=_SCRATCH, dir=out))
    try:
        (work / "old").mkdir()
        yield work
    finally:
        if not (work / "old").is_dir() or not any((work / "old").iterdir()):
            shutil.rmtree(work, ignore_errors=True)


def _replace_receipts(out: Path, staged: Path, backup: Path) -> None:
    """Swap the four receipt files within *out*; the directory itself is never renamed or removed."""
    placed: list[str] = []
    try:
        for name in _OUTPUTS:
            if (out / name).exists():
                os.replace(out / name, backup / name)
        for name in sorted(path.name for path in staged.iterdir()):
            placed.append(name)
            os.replace(staged / name, out / name)
    except BaseException as exc:
        try:
            for name in placed:
                (out / name).unlink(missing_ok=True)
            for name in _OUTPUTS:
                if (backup / name).exists():
                    os.replace(backup / name, out / name)
        except OSError as restore:
            raise _RollbackFailed(f"cannot restore previous receipts, kept at {backup}: {restore}") from exc
        raise
    shutil.rmtree(backup, ignore_errors=True)


def _write_error(out: Path, error: Exception) -> None:
    """Invalidate earlier success after an input error without inventing a measured plan."""
    with _workdir(out) as work:
        staged = Path(work) / "new"
        staged.mkdir()
        message = str(error).replace("<", "&lt;").replace(">", "&gt;")
        (staged / "report.md").write_text(
            f"# Catalog static evaluation\n\nInput error: {message}\n\n"
            "No valid result was produced. Earlier success receipts were removed.\n",
            encoding="utf-8", errors="backslashreplace")
        _replace_receipts(out, staged, Path(work) / "old")


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
    from dokimi.external.catalog_eval import evaluate_catalog, hashed_paths
    from dokimi.external.catalog_git import ignored_paths, source_and_changes
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
        source, changed, untracked, base_owners, note = source_and_changes(root, args.changed_from, out)
        ignored = ignored_paths(root) if source["commit"] else frozenset()
        report = evaluate_catalog(root, changed_paths=changed, untracked_paths=untracked, base_owners=base_owners,
                                  ignored_paths=ignored)
        if source["dirty"] is False and hashed_paths(report["skills"], untracked, ignored):
            source["dirty"] = True
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
                raise InputError(f"catalog eval: {exc}; cannot invalidate earlier receipts: {write_exc}") from write_exc
        raise InputError(f"catalog eval: {exc}") from exc
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
