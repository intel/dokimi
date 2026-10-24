# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Probes for task directories: Harbor, BenchFlow task.md, terminal-bench v1.

Harbor and task-md recognition is :func:`dokimi.harnesses.tasks.task_format` and
their checks are :func:`~dokimi.harnesses.tasks.load_task` and
:func:`~dokimi.harnesses.tasks.harbor_task_problem`: the probe reports what the
runners would do, it does not decide again.
"""

from __future__ import annotations

import warnings
from pathlib import Path
from typing import Any

from dokimi.harnesses import tasks as task_layouts
from dokimi.task_formats.model import (
    BY_BENCHFLOW, BY_DOCKER, BY_HARBOR, KIND_EXECUTABLE, Fingerprint, Report,
)
from dokimi.task_formats.source import FileSource


def _any_file(path: Path, *names: str) -> bool:
    return any((path / n).is_file() for n in names)


def _any_dir(path: Path, *names: str) -> bool:
    return any((path / n).is_dir() for n in names)


def _load(path: Path, report: Report) -> task_layouts.TaskSpec | None:
    """load_task, with its exception (and any warning it raises) as lint problems."""
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        try:
            spec = task_layouts.load_task(path)
        except (ValueError, OSError) as exc:
            report.problems.append(str(exc))
            spec = None
    report.problems.extend(str(w.message) for w in caught)
    return spec


class HarborProbe:
    name = task_layouts.FORMAT_HARBOR
    target = "dir"

    def fingerprint(self, path: Path) -> Fingerprint | None:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            fmt = task_layouts.task_format(path)
        if fmt != task_layouts.FORMAT_HARBOR:
            return None
        return Fingerprint(1.0, ("task.toml",))

    def describe(self, path: Path) -> Report:
        report = Report(path, self.name, KIND_EXECUTABLE)
        spec = _load(path, report)
        report.set_parts({
            "instruction": (path / "instruction.md").is_file(),
            "environment": (path / "environment").is_dir(),
            "oracle": _any_dir(path, "solution", "oracle"),
            "verifier": _any_dir(path, "tests", "verifier"),
            "metadata": bool(spec and spec.metadata),
        })
        if spec is None:
            return report
        report.details["metadata"] = _summary(spec.metadata)
        problem = task_layouts.harbor_task_problem(spec)
        if problem:
            report.problems.append(f"Harbor would refuse it: {problem}")
        else:
            report.consumers.append(BY_HARBOR)
        if _any_dir(path, "tests", "verifier"):
            report.consumers.append(BY_DOCKER)
        return report


class TaskMdProbe:
    name = task_layouts.FORMAT_TASK_MD
    target = "dir"

    def fingerprint(self, path: Path) -> Fingerprint | None:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            fmt = task_layouts.task_format(path)
        if fmt != task_layouts.FORMAT_TASK_MD:
            return None
        task_md = path / "task.md"
        if task_layouts._is_benchflow_task_md(task_md):
            return Fingerprint(1.0, ("task.md with BenchFlow frontmatter",))
        # task_format calls a lone task.md a task whatever it holds; a scan does not: a
        # task.md with no frontmatter and no task parts beside it is a note (a ticket in a
        # promptfoo example workspace), and one with them is a task to lint.
        parts = tuple(n for n in ("environment", "oracle", "solution", "verifier", "tests") if (path / n).is_dir())
        if task_layouts._has_frontmatter(task_md):
            return Fingerprint(0.7, ("task.md with frontmatter (not BenchFlow keys)", *parts))
        return Fingerprint(0.6, ("task.md without frontmatter", *parts)) if parts else None

    def describe(self, path: Path) -> Report:
        report = Report(path, self.name, KIND_EXECUTABLE)
        spec = _load(path, report)
        report.set_parts({
            "instruction": bool(spec and spec.instruction.strip()),
            "environment": (path / "environment").is_dir(),
            "oracle": _any_dir(path, "oracle", "solution"),
            "verifier": _any_dir(path, "verifier", "tests"),
            "metadata": bool(spec and spec.metadata),
        })
        if spec is None:
            return report
        report.details["metadata"] = _summary(spec.metadata)
        report.details["schema_version"] = spec.schema_version
        report.consumers.append(BY_BENCHFLOW)
        if _any_dir(path, "verifier", "tests"):
            report.consumers.append(BY_DOCKER)
        return report


# terminal-bench 0.x (laude-institute/terminal-bench, original-tasks/): task.yaml
# holds the instruction and metadata; the container is Dockerfile +
# docker-compose.yaml, the oracle solution.sh (or solution.yaml), the verifier
# run-tests.sh running tests/.
_TB1_SIGNALS = ("run-tests.sh", "docker-compose.yaml", "Dockerfile", "solution.sh", "solution.yaml")


class TerminalBenchV1Probe:
    name = "terminal-bench-v1"
    target = "dir"

    def fingerprint(self, path: Path) -> Fingerprint | None:
        if not (path / "task.yaml").is_file():
            return None
        seen = tuple(n for n in _TB1_SIGNALS if (path / n).is_file())
        if not seen:
            return None
        return Fingerprint(min(1.0, 0.5 + 0.1 * len(seen)), ("task.yaml", *seen))

    def describe(self, path: Path) -> Report:
        report = Report(path, self.name, KIND_EXECUTABLE)
        source = FileSource(path / "task.yaml")
        data = source.data
        report.problems.extend(f"task.yaml: {p}" for p in source.problems)
        config: dict[str, Any] = data if isinstance(data, dict) else {}
        if data is not None and not config:
            report.problems.append("task.yaml: not a mapping")
        instruction = config.get("instruction") or config.get("descriptions")
        report.set_parts({
            "instruction": bool(instruction),
            "environment": _any_file(path, "Dockerfile", "docker-compose.yaml"),
            "oracle": _any_file(path, "solution.sh", "solution.yaml"),
            "verifier": (path / "run-tests.sh").is_file(),
            "metadata": any(k in config for k in ("difficulty", "category", "tags")),
        })
        if config and not instruction:
            report.problems.append("task.yaml: no instruction")
        report.details["metadata"] = _summary(config, ("difficulty", "category", "tags", "parser_name"))
        return report


def _summary(table: dict[str, Any], keys: tuple[str, ...] = ("difficulty", "category", "tags")) -> dict[str, Any]:
    return {k: table[k] for k in keys if k in table}
