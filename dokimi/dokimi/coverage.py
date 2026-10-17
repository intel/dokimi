# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Questions-and-tasks coverage contract.

Implements the *coverage contract*: every
in-scope product declares an **awareness** signal (a golden question set) and a
**work** signal (>=1 executable terminal-bench task), and any gap is surfaced as
a checked matrix instead of being a silent default.

Design decisions taken here:

* **O4 — where the contract lives:** the canonical ``products.yaml`` registry.
  It already owns product identity and ``doc_sources`` and is the registry
  ``config_check`` treats as canonical, so the ``questions`` /
  ``coverage_status`` fields live alongside identity rather than in a third
  sync surface.
* **work signal is *derived*, not hand-listed:** a product's tasks are computed
  by scanning the ``metadata.tags`` of each task under ``<tasks-root>``
  (``task.toml`` or ``task.md``) for the product key, where
  ``<tasks-root>`` is supplied by the caller (``--tasks-root``, or the
  ``tasks_dir`` argument). This avoids making a per-product ``tasks`` list a second place task
  identity lives — the task directory stays the single source of truth.
* **roots are explicit:** awareness (``<questions-root>/<key>_golden.json``) and
  work (``<tasks-root>``) are resolved against roots the caller names; the
  evaluation content itself lives outside this repository. When a root is not
  given, that half is simply *unresolved* and the corresponding
  ``coverage_status: covered`` half-check is not applied — declaration validity
  is still checked.
* **O1 — soft enforcement:** :func:`coverage_issues` only reports a *hard* error
  for a broken declaration (a product that declares ``coverage_status: covered``
  but resolves to no task, or names a ``questions`` file that does not exist).
  A simply-missing work half is a tracked, visible ``questions-only`` gap, not a
  failure.

Awareness and work are never blended into one scalar.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from dokimi.harnesses.tasks import is_task_dir, read_task_config


_REPO_ROOT = Path(__file__).resolve().parent.parent
_PRODUCTS_PATH = Path(__file__).resolve().parent / "products.yaml"

# Honest self-report states.
STATUS_COVERED = "covered"            # has questions AND >=1 task
STATUS_QUESTIONS_ONLY = "questions-only"  # awareness only; a visible TODO
STATUS_PLANNED = "planned"            # neither yet (onboarding)
_VALID_STATUSES = {STATUS_COVERED, STATUS_QUESTIONS_ONLY, STATUS_PLANNED}


@dataclass
class ProductCoverage:
    """Resolved awareness/work coverage for one product."""

    key: str
    questions: list[str] = field(default_factory=list)  # resolvable question files
    tasks: list[str] = field(default_factory=list)       # task dir names (derived)
    declared_status: str | None = None                   # from products.yaml
    missing_questions: list[str] = field(default_factory=list)  # declared-but-absent
    # Was a root supplied for each half? A half with no root is *unresolved*
    # rather than empty, so `covered` cannot be contradicted by it.
    questions_root_given: bool = True
    tasks_root_given: bool = True

    @property
    def has_awareness(self) -> bool:
        return bool(self.questions)

    @property
    def has_work(self) -> bool:
        return bool(self.tasks)

    @property
    def effective_status(self) -> str:
        """Honest status derived from what actually resolves."""
        if self.has_awareness and self.has_work:
            return STATUS_COVERED
        if self.has_awareness:
            return STATUS_QUESTIONS_ONLY
        return STATUS_PLANNED


def _load_products(path: Path | None = None) -> dict:
    path = Path(path or _PRODUCTS_PATH)
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    products = data.get("products", {})
    return products if isinstance(products, dict) else {}


def _task_product_tags(tasks_dir: Path) -> dict[str, set[str]]:
    """Map each task dir name -> set of lowercased tags from its metadata.

    Either task layout counts (``task.toml`` or ``task.md``); tags live under
    ``metadata``. A task that does not parse is skipped, not fatal.
    """
    out: dict[str, set[str]] = {}
    if not tasks_dir.is_dir():
        return out
    for task_dir in sorted(p for p in tasks_dir.iterdir() if is_task_dir(p)):
        try:
            meta = read_task_config(task_dir)
        except (ValueError, OSError):
            continue
        # tags live under metadata; fall back to top level.
        meta_tbl = meta.get("metadata", {})
        tags = meta_tbl.get("tags") if isinstance(meta_tbl, dict) else None
        if not tags:
            tags = meta.get("tags", [])
        out[task_dir.name] = {str(t).lower() for t in (tags or []) if isinstance(t, str)}
    return out


def _tag_aliases(cfg: dict) -> set[str]:
    """Extra task tags that also indicate a task covers this product.

    Read from the product's optional ``tag_aliases:`` list in products.yaml
    (e.g. oneDAL lists ``sklearnex``, because sklearnex tasks exercise the
    oneDAL library they accelerate). Absent key -> no aliases.
    """
    declared = cfg.get("tag_aliases")
    if isinstance(declared, str):
        declared = [declared]
    if not isinstance(declared, list):
        return set()
    return {str(alias).lower() for alias in declared if isinstance(alias, str)}


def _tasks_for_product(
    key: str, task_tags: dict[str, set[str]], aliases: set[str] | None = None
) -> list[str]:
    wanted = {key} | (aliases or set())
    return sorted(name for name, tags in task_tags.items() if wanted & tags)


def _display_path(path: Path) -> str:
    """Repo-relative string for an in-repo path, absolute string otherwise."""
    try:
        return path.relative_to(_REPO_ROOT).as_posix()
    except ValueError:
        return str(path)


def resolve_coverage(
    products: dict | None = None,
    *,
    tasks_dir: Path | None = None,
    questions_dir: Path | None = None,
) -> list[ProductCoverage]:
    """Resolve per-product coverage from the registry, tasks, and question sets.

    Args:
        products: registry mapping (defaults to the canonical ``products.yaml``).
        tasks_dir: root holding one directory per executable task, each with a
            ``task.toml`` or ``task.md``. Omit it to leave the work half unresolved.
        questions_dir: root holding ``<product_key>_golden.json`` question sets.
            Omit it to leave the awareness half unresolved.

    Both roots live outside this repository; nothing is assumed about their
    location.
    """
    products = products if products is not None else _load_products()
    task_tags = _task_product_tags(Path(tasks_dir)) if tasks_dir is not None else {}
    questions_dir = Path(questions_dir) if questions_dir is not None else None

    result: list[ProductCoverage] = []
    for key, cfg in products.items():
        cfg = cfg if isinstance(cfg, dict) else {}
        declared_status = cfg.get("coverage_status")

        # Awareness: explicit `questions:` list, else the conventional golden file
        # under questions_dir. Declared relative paths resolve against the project
        # root (the directory holding pyproject.toml); absolute paths pass through.
        declared_q = cfg.get("questions")
        if isinstance(declared_q, str):
            declared_q = [declared_q]
        elif not isinstance(declared_q, list):
            declared_q = []
        questions: list[str] = []
        missing: list[str] = []
        if declared_q:
            for rel in declared_q:
                path = Path(rel)
                if not path.is_absolute():
                    path = _REPO_ROOT / rel
                if path.is_file():
                    questions.append(str(rel))
                else:  # only a *declared* path counts as missing/broken
                    missing.append(str(rel))
        elif questions_dir is not None:
            candidate = questions_dir / f"{key}_golden.json"
            if candidate.is_file():
                questions.append(_display_path(candidate))

        result.append(
            ProductCoverage(
                key=key,
                questions=questions,
                tasks=_tasks_for_product(key, task_tags, _tag_aliases(cfg)),
                declared_status=declared_status,
                missing_questions=missing,
                questions_root_given=questions_dir is not None or bool(declared_q),
                tasks_root_given=tasks_dir is not None,
            )
        )
    return result


def coverage_issues(
    coverages: list[ProductCoverage] | None = None,
    *,
    tasks_dir: Path | None = None,
    questions_dir: Path | None = None,
) -> list[str]:
    """Hard-error issues for CI.

    Only a *broken declaration* is an error:
      * ``coverage_status: covered`` but no task resolves, or no question set —
        checked only for the halves whose root was supplied;
      * a declared ``questions`` file that does not exist;
      * an unknown ``coverage_status`` value.
    A missing work half on an undeclared/`questions-only`/`planned` product is a
    tracked gap, not an error.
    """
    if coverages is None:
        coverages = resolve_coverage(tasks_dir=tasks_dir, questions_dir=questions_dir)
    issues: list[str] = []
    for c in coverages:
        if c.declared_status is not None and c.declared_status not in _VALID_STATUSES:
            issues.append(
                f"{c.key}: invalid coverage_status {c.declared_status!r} "
                f"(expected one of {sorted(_VALID_STATUSES)})"
            )
        if c.missing_questions:
            issues.append(
                f"{c.key}: declared questions file(s) not found: "
                f"{', '.join(c.missing_questions)}"
            )
        if c.declared_status == STATUS_COVERED:
            if c.tasks_root_given and not c.has_work:
                issues.append(
                    f"{c.key}: coverage_status=covered but no terminal-bench task "
                    f"tags it (work half missing)"
                )
            if c.questions_root_given and not c.has_awareness:
                issues.append(
                    f"{c.key}: coverage_status=covered but no question set resolves "
                    f"(awareness half missing)"
                )
    return issues


def coverage_matrix_rows(
    coverages: list[ProductCoverage] | None = None,
    *,
    tasks_dir: Path | None = None,
    questions_dir: Path | None = None,
) -> list[dict]:
    """Render-ready rows for the product x {questions, tasks} coverage matrix."""
    if coverages is None:
        coverages = resolve_coverage(tasks_dir=tasks_dir, questions_dir=questions_dir)
    rows = []
    for c in sorted(coverages, key=lambda x: x.key):
        rows.append(
            {
                "product": c.key,
                "awareness": "yes" if c.has_awareness else "no",
                "questions": c.questions,
                "work": len(c.tasks),
                "tasks": c.tasks,
                "status": c.effective_status,
            }
        )
    return rows


def _print_matrix(
    tasks_dir: Path | None = None, questions_dir: Path | None = None
) -> None:
    """CLI entry: print the coverage matrix as a Markdown table to stdout."""
    coverages = resolve_coverage(tasks_dir=tasks_dir, questions_dir=questions_dir)
    rows = coverage_matrix_rows(coverages)
    print("| Product | Awareness (questions) | Work (tasks) | Status |")
    print("| --- | --- | --- | --- |")
    for r in rows:
        aware = "yes" if r["awareness"] == "yes" else "no"
        print(f"| `{r['product']}` | {aware} | {r['work']} | {r['status']} |")
    if tasks_dir is None:
        print("\nNote: no --tasks-root given; the work half is unresolved.")
    if questions_dir is None:
        print("Note: no --questions-root given; the awareness half is unresolved.")
    issues = coverage_issues(coverages)
    if issues:
        print("\nISSUES:")
        for i in issues:
            print(f"  - {i}")


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m dokimi.coverage",
        description="Print the product x {awareness, work} coverage matrix.",
    )
    parser.add_argument(
        "--tasks-root",
        default=None,
        help="Directory of executable task dirs (each with task.toml or task.md) whose "
             "tags derive the work half, e.g. "
             "/path/to/content-repo/evaluation/harbor/tasks",
    )
    parser.add_argument(
        "--questions-root",
        default=None,
        help="Directory holding <product_key>_golden.json question sets that "
             "derive the awareness half, e.g. /path/to/content-repo/questions",
    )
    return parser.parse_args(argv)


if __name__ == "__main__":
    _args = _parse_args()
    _print_matrix(
        tasks_dir=Path(_args.tasks_root) if _args.tasks_root else None,
        questions_dir=Path(_args.questions_root) if _args.questions_root else None,
    )
