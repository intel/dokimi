# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Explorers: produce a ``<final_answer>`` citation block for a task.

An *explorer* takes an exploration query over a repo/doc tree and returns a
compact citation block plus any operation telemetry it emitted. Two concrete
explorers ship here:

* :class:`StaticExplorer` — returns a canned answer. Used by tests, dry runs,
  and as an oracle baseline (feed it the reference locations to get a perfect
  score, or a deliberately broad/empty answer to model a weak explorer).
* :class:`CommandExplorer` — run an external CLI (a FastContext binary,
  ``claude -p``, etc.), capture its stdout as the final answer, and ingest any
  ``DOKIMI_OP`` / ``operations.jsonl`` telemetry it produced.

Both return an :class:`ExplorerResult`, so the ExploreBench runner and the
command-wrapper harness consume one shape.
"""

from __future__ import annotations

import json
import shlex
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from dokimi import process
from dokimi.harnesses.models import OperationRecord
from dokimi.harnesses.operations import load_operations
from dokimi.metrics.exploration import CitationSet, parse_final_answer

# `timeout_sec=None` meant no timeout before the process runner, which needs one.
_NO_TIMEOUT_SEC = 7 * 24 * 3600.0


@dataclass(frozen=True)
class ExplorerResult:
    """What an explorer returns for one task."""

    final_answer: str
    operations: tuple[OperationRecord, ...] = ()
    elapsed_sec: float = 0.0
    returncode: int = 0


class Explorer(Protocol):
    """Anything that can answer an exploration query with a citation block."""

    name: str

    def explore(self, query: str, *, repo_root: Path) -> ExplorerResult:
        ...


@dataclass
class StaticExplorer:
    """Return a fixed answer regardless of the query (tests / oracle / dry run)."""

    answer: str
    name: str = "static"
    operations: tuple[OperationRecord, ...] = ()

    def explore(self, query: str, *, repo_root: Path) -> ExplorerResult:
        return ExplorerResult(final_answer=self.answer, operations=self.operations)

    @classmethod
    def from_references(cls, references, name: str = "oracle") -> "StaticExplorer":
        """Build an oracle explorer that cites exactly the reference locations."""
        lines = []
        for ref in references:
            if ref.lines:
                lines.append(f"{ref.path}:{ref.lines[0]}-{ref.lines[1]}")
            else:
                lines.append(ref.path)
        answer = "<final_answer>\n" + "\n".join(lines) + "\n</final_answer>"
        return cls(answer=answer, name=name)


@dataclass
class CommandExplorer:
    """Run a CLI explorer and capture its citation block + telemetry.

    ``command_template`` is rendered with ``{query}``, ``{query_quoted}``,
    ``{repo_root}``, and ``{output_dir}`` placeholders, then split with
    ``shlex`` and executed inside ``repo_root``. The process stdout is the final
    answer; any ``DOKIMI_OP`` markers or ``operations.jsonl`` it writes
    under ``output_dir`` are ingested as operations.
    """

    command_template: str
    name: str = "command"
    timeout_sec: float | None = 600.0  # None: no limit (bounded at a week)
    env: dict = field(default_factory=dict)
    output_root: Path | None = None
    runner: process.Runner = field(default=process.run, compare=False, repr=False)

    def explore(self, query: str, *, repo_root: Path) -> ExplorerResult:
        output_dir = self.output_root
        if output_dir is not None:
            # Resolve to absolute: the child runs with cwd=repo_root, so a
            # relative output dir/ops file would resolve against the wrong tree
            # for both the explorer's writes and our load_operations read-back.
            output_dir = output_dir.resolve()
            output_dir.mkdir(parents=True, exist_ok=True)
            # Clear any operation logs from a previous run in the same dir so
            # stale telemetry is never attached to this invocation's row.
            for name in ("operations.jsonl", "operation-log.jsonl"):
                stale = output_dir / name
                if stale.exists():
                    stale.unlink()
        context = {
            "query": query,
            "query_quoted": shlex.quote(query),
            # Forward slashes: shlex.split below would eat a Windows path's backslashes.
            "repo_root": repo_root.as_posix(),
            "output_dir": output_dir.as_posix() if output_dir else "",
        }
        try:
            command = shlex.split(_render(self.command_template, context))
        except ValueError:
            # A query with unbalanced quotes (e.g. an apostrophe inside a quoted
            # {query}) breaks shlex; fail this row, not the whole run.
            return ExplorerResult(final_answer="", operations=(), returncode=127)
        if not command:
            # An empty rendered template has nothing to run; record a failed row.
            return ExplorerResult(final_answer="", operations=(), returncode=127)
        # The explorer is the operator's own CLI: it runs with the caller's environment.
        env = dict(self.env)
        if output_dir is not None:
            env["DOKIMI_OUTPUT_DIR"] = str(output_dir)
            env["DOKIMI_OPERATIONS_FILE"] = str(output_dir / "operations.jsonl")
        start = time.time()
        try:
            # Captured whole: stdout is the answer, and the markers are read from both streams.
            proc = self.runner(command, cwd=repo_root, env=process.Env.inherit(env),
                               timeout=self.timeout_sec if self.timeout_sec is not None else _NO_TIMEOUT_SEC,
                               capture_limit=None)
        except (OSError, ValueError):
            # A missing explorer or a bad command (a NUL byte, a timeout <= 0): record a
            # failed row and let the suite continue.
            return ExplorerResult(final_answer="", operations=tuple(load_operations(output_dir, "", "")),
                                  elapsed_sec=time.time() - start, returncode=124)
        # A hung explorer is killed with its whole tree and reported as rc 124; what it
        # printed and any operations.jsonl / markers it emitted before the kill are kept.
        return ExplorerResult(
            final_answer=proc.stdout,
            operations=tuple(load_operations(output_dir, proc.stdout, proc.stderr)),
            elapsed_sec=time.time() - start,
            returncode=proc.returncode,
        )


_PLACEHOLDERS = ("query", "query_quoted", "repo_root", "output_dir")


def _render(template: str, context: dict) -> str:
    escaped = template.replace("{", "{{").replace("}", "}}")
    for key in _PLACEHOLDERS:
        escaped = escaped.replace("{{" + key + "}}", "{" + key + "}")
    return escaped.format(**context)


def build_subagent_op(
    name: str,
    cset: CitationSet,
    *,
    elapsed_sec: float = 0.0,
    status: str = "ok",
) -> OperationRecord:
    """Build the ``subagent`` telemetry op for one exploration.

    This is the bridge between the exploration step and the operation-stream
    metrics layer: a terminal-bench wrapper that delegates context discovery to
    an explorer records this op (or its marker line via :func:`op_marker_line`),
    and ``summarize_exploration`` then folds it into ``exploration_metrics``.
    """
    return OperationRecord(
        type="subagent",
        name=name,
        status=status,
        elapsed_sec=elapsed_sec,
        metadata={
            "final_answer_valid": len(cset.malformed) == 0,
            "citation_count": len(cset.citations),
            "citation_paths": sorted({c.path for c in cset.citations}),
        },
    )


def subagent_op_from_answer(
    name: str, final_answer: str, *, elapsed_sec: float = 0.0, status: str = "ok"
) -> OperationRecord:
    """Parse a ``<final_answer>`` block and build its subagent op."""
    return build_subagent_op(
        name, parse_final_answer(final_answer), elapsed_sec=elapsed_sec, status=status
    )


def op_marker_line(op: OperationRecord) -> str:
    """Render an op as an ``DOKIMI_OP`` stdout marker line.

    A wrapper that cannot write ``operations.jsonl`` can ``print`` this line; the
    harness telemetry loader parses it back into an :class:`OperationRecord`.
    """
    payload = {
        "type": op.type,
        "name": op.name,
        "status": op.status,
        "elapsed_sec": round(op.elapsed_sec, 4),
        **op.metadata,
    }
    return "DOKIMI_OP " + json.dumps(payload)
