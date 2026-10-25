# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Write a non-Harbor task in the Harbor layout, only when a Harbor-backed harness runs it.

The :class:`~.tasks.TaskSpec` is the task; Harbor's directory layout is one
place it can be written to. The docker-* harnesses run a terminal-bench v1
task as it is; ``harbor run`` and ``bench eval run`` read ``task.toml`` +
``instruction.md`` + ``environment/`` + ``tests/test.sh`` (+ ``solution/``), so
:func:`harbor_view` writes that copy for them, once per task content, into a
cache keyed by a hash of the task's files.

The mapping follows Harbor's own ``harbor.mappers.terminal_bench`` (harbor
6099b8c, 2026-10-23): the build context goes to ``environment/``, with
``ENV TEST_DIR=/tests``, the compose environment and ``working_dir`` appended to
the Dockerfile; a compose file with more than the client service is kept,
its client renamed ``main`` with Harbor's build/image/resources; ``solution.sh``
becomes ``solution/solve.sh``; ``tests/`` is copied and ``run-tests.sh``
wrapped as ``tests/test.sh``. One difference: Harbor's mapper writes the reward
from ``run-tests.sh``'s exit code, while terminal-bench reads the pytest
summary (:func:`.tbench_v1.reward`). Here ``test.sh`` applies the summary rule
too (the awk below), so a task gets the same verdict on every backend.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from dataclasses import replace
from pathlib import Path
from typing import Any

import yaml

from . import tbench_v1
from .tasks import FORMAT_HARBOR, FORMAT_TBENCH_V1, TaskSpec

#: Bump when the written layout changes, so a cached export is rewritten.
EXPORT_VERSION = "1"
_SOURCE_ONLY = {tbench_v1.CONFIG, tbench_v1.COMPOSE, "solution.sh", "solution.yaml", "run-tests.sh", "tests"}

# terminal-bench's PytestParser + _is_resolved in awk: a summary section must
# be present and list no FAILED/XPASS/ERROR test.
_VERDICT_AWK = (
    r"""awk 'BEGIN{s=0;f=0} !s && tolower($0) ~ /=+[ \t]*short test summary info[ \t]*=+/ {s=1; next} """
    r"""s {st=$1; sub(/:$/,"",st); if ((st=="FAILED"||st=="XPASS"||st=="ERROR") && NF>1) f=1} """
    r"""END{print (s && !f) ? 1 : 0}'"""
)
_TEST_SH = f"""#!/bin/bash
# Written by dokimi from a terminal-bench v1 task: run-tests.sh, scored as terminal-bench scores it.
export TEST_DIR=/tests
mkdir -p /logs/verifier
bash /tests/run-tests.sh 2>&1 | tee /logs/verifier/terminal-bench-output.txt
code=${{PIPESTATUS[0]}}
{_VERDICT_AWK} /logs/verifier/terminal-bench-output.txt > /logs/verifier/reward.txt
exit $code
"""
_HARBOR_MAIN = {
    "build": {"context": "${CONTEXT_DIR}"},
    "image": "${MAIN_IMAGE_NAME}",
    "command": ["sh", "-c", "sleep infinity"],
    "environment": ["TEST_DIR=${TEST_DIR}"],
    "deploy": {"resources": {"limits": {"cpus": "${CPUS}", "memory": "${MEMORY}"}}},
}
_HARBOR_REPLACES = {"build", "image", "container_name", "command", "environment", "volumes", "deploy"}


def harbor_view(task: TaskSpec, cache_root: Path | None = None) -> TaskSpec:
    """*task* as a Harbor-layout task: itself, or its cached Harbor copy for a terminal-bench v1 task."""
    if task.format != FORMAT_TBENCH_V1:
        return task
    root = Path(cache_root) if cache_root else default_cache_root()
    dest = root / _content_key(task.path, _task_toml(task)) / task.name
    if not (dest / "task.toml").is_file():
        dest.parent.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=".staging-", dir=dest.parent)) / task.name
        try:
            export(task, staging)
            os.replace(staging, dest)  # atomic: a concurrent run sees all of it or none
        except OSError:
            if not (dest / "task.toml").is_file():
                raise
        finally:
            shutil.rmtree(staging.parent, ignore_errors=True)
    return replace(task, path=dest, format=FORMAT_HARBOR)


def default_cache_root() -> Path:
    """``~/.cache/dokimi/harbor-export``, private: a shared temp dir lets another user plant an export."""
    root = Path.home() / ".cache" / "dokimi" / "harbor-export"
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    return root


def _content_key(path: Path, resolved: str = "") -> str:
    """Hash of the task's files and of *resolved*, what the export writes from runtime state."""
    digest = hashlib.sha256(EXPORT_VERSION.encode() + b"\0" + resolved.encode() + b"\0")
    for file in sorted(p for p in path.rglob("*") if p.is_file() and not p.is_symlink()):
        digest.update(file.relative_to(path).as_posix().encode() + b"\0")
        digest.update(hashlib.sha256(file.read_bytes()).digest())
    return digest.hexdigest()[:16]


def export(task: TaskSpec, dest: Path) -> Path:
    """Write the terminal-bench v1 *task* as a Harbor task directory at *dest* (which must not exist)."""
    if task.format != FORMAT_TBENCH_V1:
        raise ValueError(f"{task.name}: only a {FORMAT_TBENCH_V1} task is exported, not {task.format}")
    tb = task.environment.get("terminal_bench") or {}
    if tb.get("solution") == "solution.yaml":
        raise ValueError(f"{task.name}: solution.yaml (tmux keystrokes) has no Harbor equivalent; "
                         "Harbor's own mapper refuses it too")
    dest.mkdir(parents=True)
    env_dir = dest / "environment"
    _copy_context(task, env_dir)
    dockerfile = env_dir / "Dockerfile"  # the one path every Harbor environment builds
    if (env_dir / task.dockerfile).resolve() != dockerfile.resolve():
        if dockerfile.exists():
            raise ValueError(f"{task.name}: the build context has a Dockerfile besides {task.dockerfile}")
        (env_dir / task.dockerfile).rename(dockerfile)
    _append_dockerfile(dockerfile, tb)
    compose = _harbor_compose(task)
    if compose is not None:
        (env_dir / "docker-compose.yaml").write_text(yaml.safe_dump(compose, sort_keys=False), encoding="utf-8")
    if (task.path / "solution.sh").is_file():
        (dest / "solution").mkdir()
        shutil.copy(task.path / "solution.sh", dest / "solution" / "solve.sh")
    tests = dest / "tests"
    if task.tests_dir.is_dir():
        shutil.copytree(task.tests_dir, tests, symlinks=True)
    tests.mkdir(exist_ok=True)
    shutil.copy(task.path / "run-tests.sh", tests / "run-tests.sh")
    (tests / "test.sh").write_text(_TEST_SH, encoding="utf-8")
    for script in (tests / "test.sh", tests / "run-tests.sh", dest / "solution" / "solve.sh"):
        if script.is_file():
            script.chmod(0o755)
    instruction = task.instruction if task.instruction.endswith("\n") else task.instruction + "\n"
    (dest / "instruction.md").write_text(instruction, encoding="utf-8")
    (dest / "task.toml").write_text(_task_toml(task), encoding="utf-8")
    return dest


def _copy_context(task: TaskSpec, env_dir: Path) -> None:
    """The task's files into ``environment/``, the build context's at its top, as Harbor's mapper does.

    Left out: the files only the runner reads (``task.yaml``, ``run-tests.sh``, ...), except
    ``tests/`` and ``solution.sh`` when the Dockerfile copies them.
    """
    context = task.build_context
    dockerfile = (context / task.dockerfile).read_text(encoding="utf-8", errors="replace")
    keep = {n for n in ("tests", "solution.sh") for verb in ("COPY", "ADD")
            if f"{verb} {n}" in dockerfile or f"{verb} ./{n}" in dockerfile}
    inside = context.resolve() != task.path.resolve()
    skip = (_SOURCE_ONLY - keep) | ({context.relative_to(task.path).parts[0]} if inside else set())
    shutil.copytree(task.path, env_dir, symlinks=True,
                    ignore=lambda d, names: [n for n in names if Path(d) == task.path and n in skip])
    if inside:
        shutil.copytree(context, env_dir, symlinks=True, dirs_exist_ok=True)


def _append_dockerfile(dockerfile: Path, tb: dict[str, Any]) -> None:
    lines = ["ENV TEST_DIR=/tests"] + [f"ENV {k}={json.dumps(v)}" for k, v in (tb.get("env") or {}).items()]
    if tb.get("workdir"):
        lines.append(f"WORKDIR {tb['workdir']}")
    text = dockerfile.read_text(encoding="utf-8")
    dockerfile.write_text(text.rstrip("\n") + "\n\n# Mapped from docker-compose.yaml by dokimi\n"
                          + "\n".join(lines) + "\n", encoding="utf-8")


def _harbor_compose(task: TaskSpec) -> dict[str, Any] | None:
    """Harbor's compose for a multi-container task, or ``None`` when the Dockerfile is enough."""
    tb = task.environment.get("terminal_bench") or {}
    if not tb.get("unsupported"):
        return None
    data = yaml.safe_load((task.path / tbench_v1.COMPOSE).read_text(encoding="utf-8")) or {}
    services = data.get("services") or {}
    main = dict(_HARBOR_MAIN)
    main.update({k: v for k, v in (services.get(tb["service"]) or {}).items() if k not in _HARBOR_REPLACES})
    out: dict[str, Any] = {k: v for k, v in data.items() if k not in ("services", "version")}
    out["services"] = {"main": main, **{n: s for n, s in services.items() if n != tb["service"]}}
    return json.loads(json.dumps(out).replace("${T_BENCH_TASK_DOCKER_NAME_PREFIX}", "hb"))


def _task_toml(task: TaskSpec) -> str:
    def value(v: Any) -> str:
        return json.dumps(v)  # a TOML basic string, number, bool or array of them

    lines = ["[metadata]"]
    lines += [f"{k} = {value(v)}" for k, v in task.metadata.items() if v is not None]
    lines += ["", "[verifier]", f"timeout_sec = {float(task.verifier['timeout_sec'])}"]
    lines += ["", "[agent]", f"timeout_sec = {float(task.agent['timeout_sec'])}"]
    if task.environment.get("memory_mb"):
        lines += ["", "[environment]", f"memory_mb = {int(task.environment['memory_mb'])}"]
    return "\n".join(lines) + "\n"
