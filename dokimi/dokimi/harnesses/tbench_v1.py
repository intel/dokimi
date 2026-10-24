# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""terminal-bench v1 tasks (``task.yaml``), read as they are, no conversion.

A terminal-bench 0.x task (laude-institute/terminal-bench, ``original-tasks/``)
is a directory with ``task.yaml``, ``Dockerfile`` and ``docker-compose.yaml``,
``solution.sh`` (or ``solution.yaml``), ``run-tests.sh`` and ``tests/``. This
module reads one into the tables of a :class:`~.tasks.TaskSpec` and parses its
verifier output; :mod:`.tasks` builds the spec, :mod:`.harbor_export` writes the
Harbor layout for the Harbor- and BenchFlow-backed harnesses.

The semantics follow terminal-bench's own harness (``terminal_bench/``, commit
d28711d of 2026-07-10):

- ``handlers/trial_handler.py`` ``Task``: the ``task.yaml`` fields and their
  defaults (``max_agent_timeout_sec`` 360, ``max_test_timeout_sec`` 60,
  ``parser_name`` pytest).
- ``agents/oracle_agent.py``: ``solution.sh`` is copied to
  ``/oracle/solution.sh`` and run as ``bash /oracle/solution.sh`` in the
  container's shell (its ``WORKDIR``, its user).
- ``harness/harness.py`` ``_setup_test_env``/``_run_tests``: ``run-tests.sh``
  and the *contents* of ``tests/`` are copied into ``/tests``, then
  ``bash /tests/run-tests.sh`` runs with ``TEST_DIR=/tests`` (set by the
  compose file), within ``max_test_timeout_sec``.
- ``parsers/pytest_parser.py`` and ``harness._is_resolved``: the verdict is
  read from the ``short test summary info`` of ``pytest -rA``, not from the
  exit code; a task passes when every listed test passed (skipped and xfail
  count as passed), and output without that section is a fail.

Memory: terminal-bench sets no limit of its own; a compose file may set one
(``deploy.resources.limits.memory``, honoured by ``docker compose``). That
limit becomes ``environment.memory_mb``; a task without one gets
:data:`DEFAULT_MEMORY_MB` (8 GB, not the 2 GB default of other formats, which
OOM-kills tasks such as predicate-pushdown-bench), or
``$DOKIMI_TBENCH_V1_MEMORY_MB`` when set.
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

FORMAT = "terminal-bench-v1"
CONFIG = "task.yaml"
COMPOSE = "docker-compose.yaml"
TEST_DIR = "/tests"
PARSER_PYTEST = "pytest"

# trial_handler.Task defaults.
_DEFAULT_AGENT_TIMEOUT = 360.0
_DEFAULT_TEST_TIMEOUT = 60.0
#: ``environment.memory_mb`` of a task whose compose file sets no memory limit.
DEFAULT_MEMORY_MB = 8192
MEMORY_ENV = "DOKIMI_TBENCH_V1_MEMORY_MB"
_SIZE_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([kmgt]?)i?b?\s*$", re.IGNORECASE)
_SIZE_MB = {"": 1 / (1024 * 1024), "k": 1 / 1024, "m": 1, "g": 1024, "t": 1024 * 1024}
_METADATA_KEYS = (
    "author_name", "author_email", "difficulty", "category", "tags",
    "estimated_duration_sec", "expert_time_estimate_min", "junior_time_estimate_min",
)
# What terminal-bench's own compose files give the client service; the docker-*
# harnesses provide the same (TEST_DIR, the logs mounts are not used).
_DEFAULT_ENV = {"TEST_DIR=${T_BENCH_TEST_DIR}"}
_DEFAULT_VOLUMES = {
    "${T_BENCH_TASK_LOGS_PATH}:${T_BENCH_CONTAINER_LOGS_PATH}",
    "${T_BENCH_TASK_AGENT_LOGS_PATH}:${T_BENCH_CONTAINER_AGENT_LOGS_PATH}",
}
# Service keys a single `docker run` reproduces or that do not change the
# verdict (resource limits, a TTY, the restart policy).
_RUN_KEYS = {
    "build", "image", "container_name", "command", "environment", "volumes", "working_dir",
    "deploy", "tty", "stdin_open", "restart", "platform", "expose",
}
_SIGNALS = ("run-tests.sh", COMPOSE, "Dockerfile", "solution.sh", "solution.yaml")


def is_task_dir(path: Path) -> bool:
    """A ``task.yaml`` next to at least one other terminal-bench file."""
    return (path / CONFIG).is_file() and any((path / n).is_file() for n in _SIGNALS)


def read(path: Path, load_yaml: Callable[[str], Any]) -> tuple[dict[str, Any], str]:
    """The TaskSpec tables of the task at *path*, and its instruction.

    ``load_yaml`` parses YAML text (the alias-refusing loader of :mod:`.tasks`).
    Raises ``ValueError`` for a ``task.yaml`` or compose file that does not parse
    or has no instruction.
    """
    config = _mapping(_load(path / CONFIG, load_yaml), CONFIG, path)
    instruction = config.get("instruction")
    if not isinstance(instruction, str) or not instruction.strip():
        raise ValueError(f"task.yaml has no instruction: {path}")
    compose = _mapping(_load(path / COMPOSE, load_yaml), COMPOSE, path) if (path / COMPOSE).is_file() else {}
    tb = _compose_layout(compose)
    tb["run_tests_in_same_shell"] = bool(config.get("run_tests_in_same_shell"))
    tb["parser"] = str(config.get("parser_name") or PARSER_PYTEST)
    tb["solution"] = next((n for n in ("solution.sh", "solution.yaml") if (path / n).is_file()), None)
    tables = {
        "metadata": {k: config[k] for k in _METADATA_KEYS if k in config},
        "agent": {"timeout_sec": _float(config.get("max_agent_timeout_sec"), _DEFAULT_AGENT_TIMEOUT)},
        "verifier": {
            "timeout_sec": _float(config.get("max_test_timeout_sec"), _DEFAULT_TEST_TIMEOUT),
            "parser": tb["parser"],
        },
        # terminal-bench runs on compose's default network, open to the internet:
        # run-tests.sh installs curl, uv and pytest at verify time.
        "environment": {"network_mode": "public", **({"workdir": tb["workdir"]} if tb.get("workdir") else {}),
                        "memory_mb": tb.pop("memory_mb") or default_memory_mb(), "terminal_bench": tb},
    }
    return tables, instruction


def _load(file: Path, load_yaml: Callable[[str], Any]) -> Any:
    try:
        return load_yaml(file.read_text(encoding="utf-8"))
    except RecursionError as exc:
        raise ValueError(f"{file.name} is nested too deeply: {file.parent}") from exc
    except Exception as exc:  # yaml.YAMLError and the alias refusal, without importing yaml here
        raise ValueError(f"{file.name} is not valid YAML: {file.parent}: {exc}") from exc


def _mapping(data: Any, name: str, path: Path) -> dict[str, Any]:
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValueError(f"{name} must be a mapping: {path}")
    return data


def _compose_layout(compose: dict[str, Any]) -> dict[str, Any]:
    """The client service of a terminal-bench compose file, as one ``docker run`` sees it.

    ``unsupported`` lists what the docker-* harnesses' single ``docker run`` does
    not reproduce (a second service, a volume, a network, ports, tmpfs): they
    refuse such a task, the Harbor export keeps the compose file.
    """
    raw = compose.get("services")
    services: dict[str, Any] = raw if isinstance(raw, dict) else {}
    name = "client" if "client" in services else next(iter(services), None)
    found = services.get(name) if name is not None else None
    service: dict[str, Any] = found if isinstance(found, dict) else {}
    build = service.get("build")
    context = build if isinstance(build, str) else (build or {}).get("context", ".") if isinstance(build, dict) else "."
    dockerfile = (build.get("dockerfile") if isinstance(build, dict) else None) or "Dockerfile"
    unsupported = [f"service {s}" for s in services if s != name]
    unsupported += [f"top-level {k}" for k in compose if k not in ("services", "version")]
    unsupported += [f"{name}.{k}" for k in service if k not in _RUN_KEYS]
    volumes = [v for v in service.get("volumes") or [] if v not in _DEFAULT_VOLUMES]
    unsupported += [f"{name}.volumes {v}" for v in volumes]
    workdir = service.get("working_dir")
    return {
        "service": name,
        "build_context": str(context),
        "dockerfile": str(dockerfile),
        "env": _env(service.get("environment")),
        "workdir": workdir if isinstance(workdir, str) and "${" not in workdir else None,
        "unsupported": unsupported,
        "memory_mb": _size_mb(_dig(service, "deploy", "resources", "limits", "memory")),
    }


def default_memory_mb() -> int:
    """:data:`DEFAULT_MEMORY_MB`, or ``$DOKIMI_TBENCH_V1_MEMORY_MB`` when it is a positive integer."""
    raw = os.environ.get(MEMORY_ENV, "").strip()
    if not raw:
        return DEFAULT_MEMORY_MB
    if not raw.isdigit() or int(raw) <= 0:
        raise ValueError(f"{MEMORY_ENV} must be a positive number of MB, not {raw!r}")
    return int(raw)


def _dig(data: Any, *keys: str) -> Any:
    for key in keys:
        data = data.get(key) if isinstance(data, dict) else None
    return data


def _size_mb(value: Any) -> int | None:
    """A compose size (``4.0G``, ``512m``, bytes as a number) in MB, or ``None``."""
    if isinstance(value, bool) or value is None:
        return None
    match = _SIZE_RE.match(str(value))
    if not match:
        return None
    mb = int(float(match.group(1)) * _SIZE_MB[match.group(2).lower()])
    return mb if mb > 0 else None


def _env(value: Any) -> dict[str, str]:
    """The service's environment without terminal-bench's own TEST_DIR, which is fixed."""
    items = value.items() if isinstance(value, dict) else (
        (str(e).partition("=")[0], str(e).partition("=")[2]) for e in value or [] if str(e) not in _DEFAULT_ENV)
    return {str(k): "" if v is None else str(v) for k, v in items if k != "TEST_DIR"}


def _float(value: Any, default: float) -> float:
    try:
        return float(value) if value is not None else default
    except (TypeError, ValueError):
        return default


def local_docker_problem(tb: dict[str, Any], *, oracle: bool) -> str | None:
    """Why a docker-* harness cannot run this task as terminal-bench would, or ``None``."""
    if tb.get("unsupported"):
        return (f"its docker-compose.yaml uses what one docker run does not reproduce "
                f"({', '.join(tb['unsupported'])}); run it with a Harbor-backed harness")
    if tb.get("parser", PARSER_PYTEST) != PARSER_PYTEST:
        return f"its parser_name is {tb['parser']}; the docker-* harnesses read pytest output only"
    if tb.get("run_tests_in_same_shell"):
        return "run_tests_in_same_shell: its tests read the agent's shell, which a docker exec does not keep"
    if oracle and tb.get("solution") != "solution.sh":
        return ("its oracle is solution.yaml (tmux keystrokes), not solution.sh" if tb.get("solution")
                else "no solution.sh to copy to /oracle/solution.sh")
    return None


# ── verdict ─────────────────────────────────────────────────────────────────

_SUMMARY_RE = re.compile(r"=+\s*short test summary info\s*=+", re.IGNORECASE)
# PytestTestStatus: passed, skipped and xfail resolve a test; the rest fail it.
_PASSING = {"PASSED", "SKIPPED", "XFAIL"}
_STATUSES = _PASSING | {"FAILED", "XPASS", "ERROR"}


def parse_pytest(output: str) -> dict[str, bool] | None:
    """terminal-bench's ``PytestParser``: test name -> passed, or ``None`` with no summary."""
    parts = _SUMMARY_RE.split(output, maxsplit=1)
    if len(parts) < 2:
        return None
    results: dict[str, bool] = {}
    for line in parts[1].splitlines():
        if line.startswith("FAILED") and " - " in line:
            line = " - ".join(line.split(" - ")[:-1])
        fields = line.split(maxsplit=1)
        if len(fields) < 2:
            continue
        status = fields[0].strip().strip(":")
        name = fields[1].strip().split("::", 1)[-1]
        if status in _STATUSES and name:
            results[name] = status in _PASSING
    return results


def reward(output: str) -> tuple[float, dict[str, bool] | None]:
    """The terminal-bench verdict of a verifier's output: 1.0 when every parsed test passed.

    As in ``harness._is_resolved``, a summary that lists no test resolves the
    task (``all()`` of nothing) and output with no summary does not.
    """
    results = parse_pytest(output)
    return (1.0 if results is not None and all(results.values()) else 0.0), results
