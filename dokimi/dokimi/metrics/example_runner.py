# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Example extraction and validation with explicit execution policy.

Documentation can contain fenced code blocks. Executing them measures whether
examples actually run (``example_pass_rate``). Executing arbitrary code from
documentation is dangerous, so execution is governed by an explicit
:class:`ExecutionPolicy`:

* ``none`` (default) — extract and report examples but **do not execute** them.
  Safe for untrusted corpora. Examples are reported as ``skipped``.
* ``subprocess`` — run on the host in a resource-limited subprocess. Requires
  ``allow_host=True`` (an explicit opt-in). Use only for **trusted, self-owned**
  documentation. Network is *not* isolated in this mode, and the number of
  processes a snippet spawns is not capped.
* ``docker`` — run inside a locked-down container (``--network none``, no
  capabilities, ``no-new-privileges``, read-only mounts, CPU/memory/process/time
  limits). Preferred for untrusted docs.

See ``docs/example-execution.md``.
"""

from __future__ import annotations

import os
import re
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from dokimi import process
from dokimi.container_policy import ContainerPolicy


# Supported languages -> (file suffix, interpreter argv prefix for a file path).
EXECUTORS: dict[str, tuple[str, list[str]]] = {
    "python": (".py", [sys.executable or "python3"]),
    "bash": (".sh", ["bash"]),
    "sh": (".sh", ["sh"]),
}


_FENCE_OPEN_RE = re.compile(r"^(\s*)(`{3,}|~{3,})(.*)$")
_FENCE_CLOSE_RE = re.compile(r"^\s*(`{3,}|~{3,})\s*$")


class ExecutionRefused(RuntimeError):
    """The host cannot apply the policy's limits; the run stops instead of scoring examples that never ran."""


@dataclass
class ExampleResult:
    """Single example execution result.

    ``status`` is one of ``passed``/``failed``/``skipped``/``error``.
    ``passed`` is ``None`` when the example was not executed (skipped).
    """

    index: int
    lang: str
    code: str
    passed: bool | None
    status: str = "skipped"
    error: str | None = None


@dataclass
class ExecutionPolicy:
    """How (and whether) to execute fenced examples."""

    backend: str = "none"  # "none" | "subprocess" | "docker"
    timeout: int = 5
    allow_host: bool = False  # subprocess requires this explicit opt-in
    docker_image: str = "python:3.11-slim"
    mem_mb: int | None = 512
    cpus: float = 1.0
    allowed_langs: tuple[str, ...] = field(default_factory=lambda: tuple(EXECUTORS))

    @classmethod
    def from_config(cls, cfg: dict | None) -> ExecutionPolicy:
        """Build a policy from an ``example_pass_rate`` spec block.

        Accepts a nested ``execution`` block; falls back to a top-level
        ``timeout`` for backward compatibility. ``DOC_BENCH_EXAMPLE_BACKEND``
        overrides the backend (e.g. to force ``none`` in shared CI).
        """
        cfg = cfg or {}
        execution = cfg.get("execution") or {}
        timeout = int(execution.get("timeout", cfg.get("timeout", 5)))
        mem_mb = execution.get("mem_mb", 512)
        policy = cls(
            backend=str(execution.get("backend", "none")),
            timeout=timeout,
            allow_host=bool(execution.get("allow_host", False)),
            docker_image=str(execution.get("docker_image", "python:3.11-slim")),
            mem_mb=None if mem_mb is None else int(mem_mb),
            cpus=float(execution.get("cpus", 1.0)),
        )
        override = os.environ.get("DOC_BENCH_EXAMPLE_BACKEND")
        if override:
            policy.backend = override
        return policy


def _keep_example(examples: list[tuple[str, str]], opener: re.Match[str], body: list[str]) -> None:
    """Append the block as an example when its opening fence has a language tag."""
    lang = re.match(r"\w+", opener.group(3).strip())
    if lang:
        examples.append((lang.group().lower(), "\n".join(body).strip()))


def extract_examples(markdown: str) -> list[tuple[str, str]]:
    """Extract fenced code blocks with language tags from markdown.

    Supports optional metadata after the language tag (e.g.
    ```python title="x.py"```). Normalizes language tags to lowercase.
    A fence is ``` or ~~~, closed by a fence of the same character at least as
    long, so a shorter fence inside is content. Any indentation is accepted so
    that blocks nested in list items are found; each body line loses up to
    the opening fence's indentation. A fence that is never closed runs to the
    end of the document, as in CommonMark. A fence without a language tag yields
    no example.
    Returns a list of (language, code) tuples.
    """
    examples: list[tuple[str, str]] = []
    opener: re.Match[str] | None = None
    body: list[str] = []
    for line in re.split(r"\r\n|\r|\n", markdown):
        if opener is None:
            m = _FENCE_OPEN_RE.match(line)
            if m and not (m.group(2)[0] == "`" and "`" in m.group(3)):
                opener, body = m, []
            continue
        close = _FENCE_CLOSE_RE.match(line)
        fence = opener.group(2)
        if close and close.group(1)[0] == fence[0] and len(close.group(1)) >= len(fence):
            _keep_example(examples, opener, body)
            opener = None
        else:
            # Drop up to the opener's indentation, as CommonMark does, so a
            # block nested in a list item keeps its relative indentation.
            indent = len(opener.group(1))
            stripped = len(line) - len(line.lstrip())
            body.append(line[min(indent, stripped):])
    if opener is not None:
        _keep_example(examples, opener, body)
    return examples


def _run_subprocess(lang: str, code: str, policy: ExecutionPolicy,
                    runner: process.Runner) -> tuple[bool | None, str | None]:
    """Run code on the host in a resource-limited subprocess (trusted docs only).

    CPU time is capped; address space is capped unless explicitly omitted.
    There is no RLIMIT_NPROC: it counts every process and thread of the real UID system-wide (and is not
    enforced for root), not the snippet's own children, so a small cap makes every
    fork fail on a busy host. Capping the process count is the docker backend's job
    (``--pids-limit``). The runner kills the snippet's whole process group when it
    exits or times out, so a background job it started neither outlives it nor keeps
    the run waiting. ``None`` means the requested limits cannot be applied here, so nothing ran.
    """
    if not policy.allow_host:
        return False, (
            "host execution refused: subprocess backend requires allow_host=True "
            "(only enable for trusted, self-owned documentation)"
        )

    suffix, argv_prefix = EXECUTORS[lang]
    # Minimal environment — do not leak the parent's secrets/API keys. LD_LIBRARY_PATH
    # is preserved (not secret) because relocatable Python builds — e.g. actions/setup-python's
    # hostedtoolcache installs — need it so the dynamic loader can find libpython*.so.
    memory_bytes = None if policy.mem_mb is None else policy.mem_mb * 1024 * 1024
    limits = process.Limits(cpu_sec=max(1, policy.timeout), memory_bytes=memory_bytes)
    if memory_bytes is not None and memory_bytes <= 0:
        return None, "not run: mem_mb must be positive"
    try:
        process.check_limits(limits)
    except ValueError as exc:
        hint = "; set mem_mb: null to keep only the CPU cap" if sys.platform == "darwin" else ""
        return None, f"not run: {exc}{hint}"
    with tempfile.TemporaryDirectory() as tmp:
        # HOME points at the per-run temp dir rather than a shared /tmp.
        env = process.Env.only(("LD_LIBRARY_PATH",),
                               {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": tmp})
        script = Path(tmp) / f"snippet{suffix}"
        script.write_text(code, encoding="utf-8")
        try:  # stdout is not reported
            proc = runner(argv_prefix + [str(script)], cwd=tmp, env=env, timeout=policy.timeout,
                          capture_stdout=False, limits=limits)
        except Exception as exc:  # noqa: BLE001 - report any spawn failure
            return False, f"Execution error: {type(exc).__name__}: {exc}"

    if proc.timed_out:
        return False, f"Timeout after {policy.timeout}s"
    if proc.returncode == 0:
        return True, None
    return False, f"Exit code {proc.returncode}: {proc.stderr[:200]}"


def _run_docker(lang: str, code: str, policy: ExecutionPolicy,
                runner: process.Runner) -> tuple[bool | None, str | None]:
    """Run code inside a locked-down container (no network, no capabilities, read-only, limited)."""
    if policy.mem_mb is None or policy.mem_mb <= 0:
        return None, "not run: docker backend requires a positive memory cap (mem_mb)"
    suffix, argv_prefix = EXECUTORS[lang]
    with tempfile.TemporaryDirectory() as tmp:
        script = Path(tmp) / f"snippet{suffix}"
        script.write_text(code, encoding="utf-8")
        # Root without CAP_DAC_OVERRIDE cannot enter a 0700 dir it does not own (#229),
        # so the :ro mount is world-readable; it holds only the document's own snippet.
        os.chmod(tmp, 0o755)  # nosec B103 - read and entered as "other", not writable
        script.chmod(0o644)
        flags = ContainerPolicy(network="none", no_new_privileges=True, cap_drop_all=True, cpus=policy.cpus,
                                memory_mb=policy.mem_mb, pids_limit=64, read_only=True)
        cmd = [
            "docker", "run", "--rm",
            *flags.hardening_argv(),
            *flags.network_argv(),
            *flags.resource_argv(),
            *flags.pids_argv(),
            *flags.read_only_argv(),
            "-v", f"{tmp}:/work:ro",
            policy.docker_image,
            "timeout", str(policy.timeout),
            *argv_prefix, f"/work/{script.name}",
        ]
        try:  # the docker CLI needs the caller's DOCKER_HOST / config; the snippet sees none of it
            result = runner(cmd, timeout=policy.timeout + 30, env=process.Env.inherit())
        except FileNotFoundError:
            return False, "docker backend selected but docker is not installed"
        except Exception as exc:  # noqa: BLE001
            return False, f"Execution error: {type(exc).__name__}: {exc}"

    if result.timed_out:
        return False, f"Timeout after {policy.timeout}s"
    if result.returncode == 0:
        return True, None
    return False, f"Exit code {result.returncode}: {result.stderr[:200]}"


def run_example(lang: str, code: str, policy: ExecutionPolicy,
                runner: process.Runner = process.run) -> ExampleResult:
    """Execute (or skip) a single example according to ``policy``.

    Raises :class:`ExecutionRefused` when the host cannot apply the requested limits.
    """
    if lang not in policy.allowed_langs or lang not in EXECUTORS:
        return ExampleResult(0, lang, code[:100], passed=None, status="skipped",
                             error=f"Unsupported or disallowed language: {lang}")

    if policy.backend == "none":
        return ExampleResult(0, lang, code[:100], passed=None, status="skipped",
                             error="execution disabled (backend=none)")

    if policy.backend == "subprocess":
        ok, err = _run_subprocess(lang, code, policy, runner)
    elif policy.backend == "docker":
        ok, err = _run_docker(lang, code, policy, runner)
    else:
        return ExampleResult(0, lang, code[:100], passed=None, status="error",
                             error=f"Unknown execution backend: {policy.backend}")

    if ok is None:  # neither 0.0 nor a perfect score for a snippet that never ran
        raise ExecutionRefused(f"{lang} example {err}")
    return ExampleResult(0, lang, code[:100], passed=ok,
                         status="passed" if ok else "failed", error=err)


def score_examples(
    doc_path: Path, policy: ExecutionPolicy | None = None, timeout: int | None = None
) -> tuple[float, list[ExampleResult]]:
    """Run all examples in a document; return (pass_rate, results).

    ``pass_rate`` is computed over *executed* examples only. A document with no
    examples — or whose examples were all skipped — scores 1.0 (nothing failed).
    For backward compatibility, a bare ``timeout`` may be passed instead of a
    policy (implies the legacy host-subprocess behavior, opt-in). Limits the host
    cannot apply raise :class:`ExecutionRefused`.
    """
    if policy is None:
        policy = ExecutionPolicy(
            backend="subprocess" if timeout is not None else "none",
            timeout=timeout or 5,
            allow_host=timeout is not None,
        )

    text = doc_path.read_text(encoding="utf-8", errors="replace")
    examples = extract_examples(text)
    if not examples:
        return 1.0, []

    results: list[ExampleResult] = []
    for idx, (lang, code) in enumerate(examples):
        res = run_example(lang, code, policy)
        res.index = idx
        results.append(res)

    # "error" rows (e.g. unknown backend) count as executed failures so a broken
    # config never reports a perfect pass rate; "skipped" rows are excluded.
    executed = [r for r in results if r.status in ("passed", "failed", "error")]
    if not executed:
        return 1.0, results
    passed = sum(1 for r in executed if r.status == "passed")
    return round(passed / len(executed), 4), results
