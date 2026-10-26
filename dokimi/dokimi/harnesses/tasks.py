# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Executable task discovery and metadata loading.

Two on-disk layouts are understood and normalised into one :class:`TaskSpec`:

``harbor``
    The split Harbor/terminal-bench layout: ``task.toml`` + ``instruction.md``,
    with ``solution/solve.sh`` and ``tests/test.sh``.
``task-md``
    The BenchFlow unified layout used by SkillsBench: a single ``task.md``
    whose YAML frontmatter carries the same tables (``metadata``, ``verifier``,
    ``agent``, and ``sandbox`` — called ``environment`` before schema 1.3) and
    whose body (its ``## prompt`` section, when it has one) is the instruction,
    with ``oracle/solve.sh`` and ``verifier/test.sh``.

A directory holding both is loaded as ``task-md`` (the format BenchFlow treats
as canonical when it migrates a split task) when the ``task.md`` frontmatter
is a BenchFlow document (every top-level key one BenchFlow knows); any other
``task.md`` there is an author's note and the task loads from ``task.toml``.
When both definitions load, :func:`legacy_drift` names where they differ.

``terminal-bench-v1``
    terminal-bench 0.x: ``task.yaml`` + ``Dockerfile``/``docker-compose.yaml``
    + ``solution.sh`` + ``run-tests.sh`` + ``tests/``, read by :mod:`.tbench_v1`.
    The docker-* harnesses run it as it is (:attr:`TaskSpec.oracle_step`,
    :attr:`TaskSpec.verifier_step`); the Harbor- and BenchFlow-backed ones get
    a Harbor copy (:mod:`.harbor_export`).
"""

from __future__ import annotations

import datetime
import re
import tomllib
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from . import tbench_v1

# Executable tasks live in a separate evaluation-content checkout, so there is
# no in-repo default root: the caller always names one.
NO_TASKS_ROOT_MESSAGE = (
    "no tasks root given; pass --tasks-root <checkout>/evaluation/harbor/tasks "
    "(or a full path to a single task directory)"
)

FORMAT_HARBOR = "harbor"
FORMAT_TASK_MD = "task-md"
FORMAT_TBENCH_V1 = tbench_v1.FORMAT
PARSER_REWARD_FILE = "reward-file"

# Where each layout keeps its oracle and its verifier, in lookup order. Both
# names are tried for either format, as BenchFlow accepts both. The order
# differs from BenchFlow's in one case: BenchFlow always prefers oracle/ and
# verifier/, while a Harbor-layout directory that holds both pairs uses
# solution/ and tests/ here, the names Harbor itself reads.
_SOLUTION_DIRS = {FORMAT_HARBOR: ("solution", "oracle"), FORMAT_TASK_MD: ("oracle", "solution"),
                  FORMAT_TBENCH_V1: ("solution",)}
_TESTS_DIRS = {FORMAT_HARBOR: ("tests", "verifier"), FORMAT_TASK_MD: ("verifier", "tests"),
               FORMAT_TBENCH_V1: ("tests",)}


@dataclass(frozen=True)
class ContainerStep:
    """What a docker-* run copies into the task container and runs, for the oracle or the verifier.

    ``copies`` maps a host file or directory to a container path (a
    directory's contents go into it); ``user`` ``None`` is the image's own
    user; ``parser`` says how the verdict is read: the reward file, or
    ``pytest`` (terminal-bench's summary parser, :func:`.tbench_v1.reward`).
    """

    copies: tuple[tuple[Path, str], ...]
    command: str
    user: str | None = "root"
    parser: str = PARSER_REWARD_FILE


@dataclass(frozen=True)
class TaskSpec:
    """Resolved executable task descriptor, whichever layout it came from."""

    name: str
    path: Path
    instruction: str
    metadata: dict[str, Any] = field(default_factory=dict)
    verifier: dict[str, Any] = field(default_factory=dict)
    agent: dict[str, Any] = field(default_factory=dict)
    environment: dict[str, Any] = field(default_factory=dict)
    oracle: dict[str, Any] = field(default_factory=dict)
    format: str = FORMAT_HARBOR
    schema_version: str | None = None

    @property
    def timeout_sec(self) -> float | None:
        value = self.agent.get("timeout_sec")
        return float(value) if value is not None else None

    @property
    def solution_dir(self) -> Path:
        return _first_dir(self.path, _SOLUTION_DIRS[self.format])

    @property
    def tests_dir(self) -> Path:
        return _first_dir(self.path, _TESTS_DIRS[self.format])

    @property
    def _tb(self) -> dict[str, Any]:
        return _table(self.environment.get("terminal_bench")) if self.format == FORMAT_TBENCH_V1 else {}

    @property
    def build_context(self) -> Path:
        """The ``docker build`` context: ``environment/``, or the compose file's."""
        if self.format == FORMAT_TBENCH_V1:
            return self.path / str(self._tb.get("build_context") or ".")
        return self.path / "environment"

    @property
    def dockerfile(self) -> str:
        """The Dockerfile, relative to :attr:`build_context`."""
        return str(self._tb.get("dockerfile") or "Dockerfile")

    @property
    def container_env(self) -> dict[str, str]:
        """Environment the task container starts with (terminal-bench's ``TEST_DIR`` and compose env)."""
        if self.format != FORMAT_TBENCH_V1:
            return {}
        return {**_table(self._tb.get("env")), "TEST_DIR": tbench_v1.TEST_DIR}

    @property
    def oracle_step(self) -> ContainerStep:
        if self.format == FORMAT_TBENCH_V1:
            return ContainerStep(((self.path / "solution.sh", "/oracle/solution.sh"),),
                                 "bash /oracle/solution.sh", user=None)
        return ContainerStep(((self.solution_dir, "/solution"), (self.solution_dir, "/oracle")),
                             "chmod +x /solution/solve.sh && /solution/solve.sh")

    @property
    def verifier_step(self) -> ContainerStep:
        if self.format == FORMAT_TBENCH_V1:
            copies: tuple[tuple[Path, str], ...] = ((self.path / "run-tests.sh", f"{tbench_v1.TEST_DIR}/run-tests.sh"),)
            if self.tests_dir.is_dir():
                copies += ((self.tests_dir, tbench_v1.TEST_DIR),)
            # 2>&1: one ordered stream, as terminal-bench reads it from the tmux pane
            return ContainerStep(copies, f"bash {tbench_v1.TEST_DIR}/run-tests.sh 2>&1", user=None,
                                 parser=tbench_v1.PARSER_PYTEST)
        return ContainerStep(((self.tests_dir, "/tests"), (self.tests_dir, "/verifier")),
                             "mkdir -p /logs/verifier && chmod +x /tests/test.sh && /tests/test.sh")

    def local_docker_problem(self, *, oracle: bool) -> str | None:
        """Why a docker-* harness cannot run this task as its own runner would, or ``None``."""
        if self.format != FORMAT_TBENCH_V1:
            return None
        return tbench_v1.local_docker_problem(self._tb, oracle=oracle)

    @property
    def skills_dir(self) -> Path | None:
        """Task-bundled skills (``environment/skills``), if the task ships any."""
        path = self.path / "environment" / "skills"
        return path if path.is_dir() else None

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "path": str(self.path),
            "format": self.format,
            "schema_version": self.schema_version,
            "metadata": self.metadata,
            "verifier": self.verifier,
            "agent": self.agent,
            "environment": self.environment,
            "oracle": self.oracle,
        }


def task_format(path: Path) -> str | None:
    """The layout of the task directory at *path*, or ``None`` if it is not one."""

    path = Path(path)
    has_toml = (path / "task.toml").is_file()
    if (path / "task.md").is_file():
        # Next to a task.toml, a task.md is a BenchFlow task only if its
        # frontmatter is a BenchFlow document. Anything else (no frontmatter,
        # Jekyll/Hugo frontmatter, a leading horizontal rule, an unclosed
        # block) is an author's note: the directory loads from task.toml, as
        # it did before task.md was read at all.
        if not has_toml or _is_benchflow_task_md(path / "task.md"):
            return FORMAT_TASK_MD
    if has_toml:
        return FORMAT_HARBOR
    if tbench_v1.is_task_dir(path):
        return FORMAT_TBENCH_V1
    return None


def _has_frontmatter(path: Path) -> bool:
    with path.open(encoding="utf-8", errors="replace") as fh:
        return fh.readline().strip() == "---"


# Top-level keys of a BenchFlow 0.7.8 task.md: the TaskConfig fields
# (benchflow/task/config.py, with its 'solution' alias of 'oracle' and the
# 'version' spelling of 'schema_version'), the document keys
# (task/_document_parse.py) and the authoring keys (task/_document_profiles.py;
# _reject_authoring_shorthands refuses all of them but 'name'), plus the
# pre-1.3 'environment' spelling of 'sandbox' that load_task accepts.
_BENCHFLOW_KEYS = frozenset(
    {
        "schema_version", "version", "task", "metadata", "verifier", "agent", "sandbox",
        "environment", "oracle", "solution", "source", "reward",
        "multi_step_reward_strategy", "steps", "artifacts",
        "agents", "benchflow", "scenes", "user",
        "image", "name", "profile", "profiles",
    }
)
# Of those, the tables BenchFlow only accepts as mappings.
_BENCHFLOW_TABLES = frozenset({"task", "metadata", "agent", "sandbox", "environment"})

# task_format runs several times per directory (discovery, load_task,
# read_task_config); warn about each ignored file once per process.
_WARNED: set[str] = set()


def _warn_once(message: str) -> None:
    if message not in _WARNED:
        _WARNED.add(message)
        warnings.warn(message, stacklevel=3)


def _is_benchflow_task_md(path: Path) -> bool:
    """Whether a ``task.md`` next to a ``task.toml`` is the task (see task_format)."""
    if not _has_frontmatter(path):
        return False  # plain notes: nothing to say
    try:
        data = _split_frontmatter(path)[0]
    except ValueError as exc:
        _warn_once(f"ignoring task.md next to task.toml, loading task.toml: {exc}")
        return False
    unknown = sorted(str(k) for k in data if k not in _BENCHFLOW_KEYS)
    not_tables = sorted(k for k in _BENCHFLOW_TABLES if k in data and not isinstance(data[k], dict))
    if data and not unknown and not not_tables:
        return True
    if not data:
        why = "its frontmatter is empty"
    elif unknown:
        why = f"its frontmatter has keys BenchFlow does not know ({', '.join(unknown)})"
    else:
        why = f"its frontmatter has non-table {', '.join(not_tables)}"
    _warn_once(f"ignoring task.md next to task.toml, loading task.toml: {why}: {path}")
    return False


def is_task_dir(path: Path) -> bool:
    return task_format(path) is not None


def discover_tasks(root: Path | None = None) -> list[TaskSpec]:
    """Load every task directory under *root* (either layout).

    Args:
        root: tasks root to scan. Required — there is no bundled task tree.

    Raises:
        ValueError: if *root* is None.
    """

    if root is None:
        raise ValueError(NO_TASKS_ROOT_MESSAGE)
    root = Path(root)
    if not root.is_dir():
        return []
    tasks = []
    for path in sorted(root.iterdir()):
        fmt = task_format(path)
        if fmt is None:
            continue
        if fmt == FORMAT_TASK_MD and not _has_frontmatter(path / "task.md"):
            # A lone task.md without frontmatter (planning notes, say) is not
            # a task. BenchFlow's batch discovery skips it too; load_task on
            # that directory still raises.
            _warn_once(f"skipping {path}: task.md has no YAML frontmatter")
            continue
        if fmt == FORMAT_TBENCH_V1 and not tbench_v1.has_instruction(path, _load_yaml):
            _warn_once(f"skipping {path}: task.yaml has no instruction")  # some other tool's task.yaml
            continue
        tasks.append(load_task(path))
    return tasks


def _read_tables(path: Path, fmt: str) -> tuple[dict[str, Any], str]:
    """The raw tables and instruction of the task at *path*, read by its *fmt*."""
    if fmt == FORMAT_TASK_MD:
        return _read_task_md(path / "task.md")
    if fmt == FORMAT_TBENCH_V1:
        return tbench_v1.read(path, _load_yaml)
    instruction_path = path / "instruction.md"
    if not instruction_path.is_file():
        raise FileNotFoundError(f"Task has no instruction.md: {path}")
    return read_task_config(path), instruction_path.read_text(encoding="utf-8")


def load_task(path_or_name: str | Path, root: Path | None = None) -> TaskSpec:
    """Load one task by directory path, or by task name relative to *root*.

    A full path is loaded without a *root*; a bare task name needs one.
    """

    path = Path(path_or_name)
    if not path.exists():
        if root is None:
            # A bare task name cannot be resolved without a root; a path that
            # was spelled out is simply missing.
            if not path.is_absolute() and len(path.parts) == 1:
                raise ValueError(NO_TASKS_ROOT_MESSAGE)
            raise FileNotFoundError(f"Task directory not found: {path_or_name}")
        path = Path(root) / str(path_or_name)
    path = path.resolve()
    if not path.is_dir():
        raise FileNotFoundError(f"Task directory not found: {path_or_name}")

    fmt = task_format(path)
    if fmt is None:
        raise FileNotFoundError(f"Task has no task.toml, task.md or task.yaml: {path}")
    data, instruction = _read_tables(path, fmt)

    # BenchFlow schema 1.3 renamed [environment] to [sandbox]; either feeds
    # TaskSpec.environment. BenchFlow rejects 'environment:' inside a task.md;
    # it is accepted here so SkillsBench snapshots written before the rename
    # still load. Declaring both is rejected, as BenchFlow does:
    # they are one section, and a silent merge would accept a file BenchFlow
    # refuses to run.
    if isinstance(data.get("environment"), dict) and isinstance(data.get("sandbox"), dict):
        raise ValueError(
            f"task declares both 'environment' and 'sandbox' (the same section; "
            f"'environment' is the legacy Harbor spelling); keep only 'sandbox': {path}"
        )
    environment = _table(data.get("sandbox")) or _table(data.get("environment"))
    version = data.get("schema_version")
    spec = TaskSpec(
        name=path.name,
        path=path,
        instruction=instruction,
        metadata=_table(data.get("metadata")),
        verifier=_table(data.get("verifier")),
        agent=_table(data.get("agent")),
        environment=environment,
        oracle=_table(data.get("oracle")),
        format=fmt,
        schema_version=str(version) if version is not None else None,
    )
    drift = legacy_drift(spec)
    if drift:
        _warn_once(
            f"task.md and the task.toml/instruction.md next to it differ in {', '.join(drift)}: "
            f"docker-* and the run artifact use task.md, Harbor-backed harnesses refuse it: {path}"
        )
    return spec


def read_task_config(path: Path) -> dict[str, Any]:
    """The raw config tables of a task directory, without the instruction.

    For scanners that only need metadata: unlike :func:`load_task` it does not
    require ``instruction.md``. Raises ``ValueError`` on an unparsable file and
    ``FileNotFoundError`` when *path* holds neither layout.
    """

    path = Path(path)
    fmt = task_format(path)
    if fmt == FORMAT_TASK_MD:
        return _read_task_md(path / "task.md")[0]
    if fmt == FORMAT_HARBOR:
        return _read_task_toml(path)
    if fmt == FORMAT_TBENCH_V1:
        return tbench_v1.read(path, _load_yaml)[0]
    raise FileNotFoundError(f"Task has no task.toml, task.md or task.yaml: {path}")


def _read_task_toml(path: Path) -> dict[str, Any]:
    """The ``task.toml`` of task directory *path*; ``ValueError`` if unparsable."""

    try:
        data = tomllib.loads((path / "task.toml").read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"task.toml is not valid TOML: {path}: {exc}") from exc
    except RecursionError as exc:
        raise ValueError(f"task.toml is nested too deeply: {path}") from exc
    return _plain(data)


def legacy_drift(task: TaskSpec) -> list[str]:
    """Where a ``task.md`` task and the split definition next to it disagree.

    ``bench tasks migrate`` without ``--remove-legacy`` leaves ``task.toml`` and
    ``instruction.md`` beside the new ``task.md``. The ``docker-*`` harnesses
    and the run artifact use the ``task.md``; ``harbor run`` reads the split
    files. BenchFlow's ``bench tasks check`` rejects such a directory when the
    two differ, and so do the Harbor-backed harnesses here. This compares what
    a run uses (the ``metadata``, ``verifier``, ``agent``, sandbox and
    ``oracle`` tables, and the prompt as BenchFlow normalises it), not every
    key BenchFlow compares. Returns the differing sections (``task.toml`` when
    it does not parse), and ``[]`` for any other task.
    """

    if task.format != FORMAT_TASK_MD or not (task.path / "task.toml").is_file():
        return []
    try:
        legacy = _read_task_toml(task.path)
    except ValueError:
        return ["task.toml"]
    ours = {
        "metadata": task.metadata,
        "verifier": task.verifier,
        "agent": task.agent,
        "environment": task.environment,
        "oracle": task.oracle,
    }
    theirs = {
        "metadata": _table(legacy.get("metadata")),
        "verifier": _table(legacy.get("verifier")),
        "agent": _table(legacy.get("agent")),
        "environment": _table(legacy.get("sandbox")) or _table(legacy.get("environment")),
        "oracle": _table(legacy.get("oracle")) or _table(legacy.get("solution")),
    }
    drift = [key for key in ours if ours[key] != theirs[key]]
    instruction = task.path / "instruction.md"
    if instruction.is_file():
        try:
            prompt = instruction.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            prompt = None
        if prompt is None or _normalize_prompt(prompt) != _normalize_prompt(task.instruction):
            drift.append("instruction")
    return drift


def _normalize_prompt(text: str) -> str:
    """BenchFlow's prompt comparison (task/runtime_capabilities.py)."""
    return "\n".join(line.rstrip() for line in text.strip().splitlines())


def harbor_task_problem(task: TaskSpec, *, needs_solution: bool = False) -> str | None:
    """Why ``harbor run -p <task>`` would not run *task*, or ``None``.

    Mirrors the file checks of Harbor 0.24's ``Task.is_valid_dir``: a
    ``task.toml``, an ``environment/`` directory, and (for a task without
    ``steps``, whose per-step files are left to Harbor) an ``instruction.md``
    and a ``tests/test.sh`` (``test.bat`` for a Windows verifier), unless a
    separate-mode verifier bundles its tests. Harbor's oracle agent also needs
    a solve script (*needs_solution*). Harbor's own rejection exits 1, which a
    Harbor-backed cell would record as a fail, so these are refused before
    Harbor starts. So are scripts that read BenchFlow's mounts
    (:func:`benchflow_mount_refs`), which Harbor does not provide, and a
    ``task.md`` task whose split definition, the one Harbor reads, differs
    from it (:func:`legacy_drift`). Not mirrored: Harbor's schema validation
    of the config values, per-step directories, and symlinks leaving the task.
    """

    path = task.path
    if not (path / "task.toml").is_file():
        return "no task.toml"
    try:
        config = _read_task_toml(path)
    except ValueError as exc:
        return str(exc)
    missing = [] if (path / "environment").is_dir() else ["environment/"]
    if not config.get("steps"):
        if not (path / "instruction.md").is_file():
            missing.append("instruction.md")
        # Harbor's verifier_mode.py: a [verifier.environment] table implies the
        # separate mode, and only a separate verifier can bundle its tests.
        verifier = _table(config.get("verifier"))
        verifier_env = _table(verifier.get("environment"))
        has_env = "environment" in verifier
        mode = verifier.get("environment_mode") or ("separate" if has_env else "shared")
        bundled = mode == "separate" and (
            bool(verifier_env.get("docker_image"))
            or any((path / "tests" / n).is_file() for n in ("Dockerfile", "docker-compose.yaml"))
        )
        task_os = str(_table(config.get("environment")).get("os") or "linux")
        test_os = str(verifier_env.get("os") or "linux") if mode == "separate" and has_env else task_os
        test_script = "test.bat" if test_os == "windows" else "test.sh"
        if not bundled and not (path / "tests" / test_script).is_file():
            missing.append(f"tests/{test_script}")
        # The oracle agent takes the OS's solve script, else solve.sh.
        solve = {"solve.sh", "solve.bat" if task_os == "windows" else "solve.sh"}
        if needs_solution and not any((path / "solution" / n).is_file() for n in solve):
            missing.append("solution/solve.bat or solve.sh" if task_os == "windows" else "solution/solve.sh")
    if missing:
        return f"no {', '.join(missing)}"
    scripts = [("tests", "test.sh")] + ([("solution", "solve.sh")] if needs_solution else [])
    for directory, script in scripts:
        hits = benchflow_mount_refs(path / directory / script)
        if hits:
            return (
                f"{directory}/{script} reads {', '.join(hits)} (BenchFlow's paths, as `bench tasks "
                f"export --target harbor` leaves them); Harbor mounts {directory}/ at /{directory}"
            )
    drift = legacy_drift(task)
    if drift:
        return (
            f"its task.md (what the run records) and task.toml/instruction.md (what Harbor runs) "
            f"differ in {', '.join(drift)}"
        )
    return None


# A path under BenchFlow's /oracle or /verifier mount: not /logs/verifier (the
# shared reward dir), nor a path under a variable, a subshell or ~.
_BENCHFLOW_MOUNT_RE = re.compile(r"(?<![\w.$/})~])/(?:oracle|verifier)(?![\w.-])")


def benchflow_mount_refs(script: Path) -> list[str]:
    """The BenchFlow mounts (``/oracle``, ``/verifier``) *script*'s code uses.

    ``bench tasks export --target harbor`` renames ``oracle/`` and
    ``verifier/`` to ``solution/`` and ``tests/`` but leaves these paths in the
    scripts, so a runner that mounts the Harbor names verifies against files
    that are not there. Comment lines are skipped; a missing script has none.
    """
    if not script.is_file():
        return []
    code = (
        line for line in script.read_text(encoding="utf-8", errors="replace").splitlines()
        if not line.lstrip().startswith("#")
    )
    return sorted({m.group(0) for line in code for m in _BENCHFLOW_MOUNT_RE.finditer(line)})


def _read_task_md(path: Path) -> tuple[dict[str, Any], str]:
    """Split a ``task.md`` into its YAML frontmatter and its instruction body."""

    data, body = _split_frontmatter(path)
    _reject_authoring_shorthands(data, path)
    return _plain(data), _prompt_section(body, path)


def _split_frontmatter(path: Path) -> tuple[dict[str, Any], str]:
    """A ``task.md``'s frontmatter mapping and its raw body; ``ValueError`` if malformed."""

    text = path.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        raise ValueError(f"task.md has no YAML frontmatter: {path}")
    for end in range(1, len(lines)):
        if lines[end].strip() == "---":
            break
    else:
        raise ValueError(f"task.md frontmatter is not closed with '---': {path}")
    try:
        data = _load_yaml("".join(lines[1:end]))
    except _AliasRefused as exc:
        raise ValueError(f"task.md frontmatter uses a YAML alias, which is refused: {path}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise ValueError(f"task.md frontmatter is not valid YAML: {path}: {exc}") from exc
    except RecursionError as exc:
        raise ValueError(f"task.md frontmatter is nested too deeply: {path}") from exc
    if data is None:  # empty or comment-only frontmatter
        data = {}
    if not isinstance(data, dict):
        raise ValueError(f"task.md frontmatter must be a mapping: {path}")
    return data, "".join(lines[end + 1:]).lstrip("\n")


class _AliasRefused(yaml.YAMLError):
    pass


class _NoAliasLoader(yaml.SafeLoader):
    """``SafeLoader`` that refuses aliases.

    Task content comes from third-party catalogs, and a few hundred bytes of
    nested aliases expand to exponential time and memory; a self-referencing
    one recurses without end. BenchFlow's own loader resolves them, but no
    SkillsBench task uses one.
    """

    def compose_node(self, parent: yaml.Node | None, index: Any) -> yaml.Node | None:
        if self.check_event(yaml.AliasEvent):
            raise _AliasRefused(f"alias {self.peek_event().start_mark}")
        return super().compose_node(parent, index)


def _load_yaml(text: str) -> Any:
    loader = _NoAliasLoader(text)
    try:
        return loader.get_single_data()
    finally:
        loader.dispose()



# BenchFlow's reserved body headings (benchflow.task._document_parse). Only
# ``## prompt`` (or, without it, the text before the first heading) is the
# agent's instruction; role, scene and user-persona sections are for other
# participants and must not reach the solver.
_SECTION_RE = re.compile(
    r"^##\s+(prompt|role:[A-Za-z0-9_.-]+|scene:[A-Za-z0-9_.-]+|user-persona)\s*$",
    re.IGNORECASE | re.MULTILINE,
)
_ESCAPED_SECTION_RE = re.compile(
    r"^\\(##\s+(?:prompt|role:[A-Za-z0-9_.-]+|scene:[A-Za-z0-9_.-]+|user-persona)\s*)$",
    re.IGNORECASE | re.MULTILINE,
)


def _prompt_section(body: str, path: Path) -> str:
    """The instruction BenchFlow would give the agent for this body."""

    matches = list(_SECTION_RE.finditer(body))
    if not matches:
        return body.strip()  # BenchFlow strips the prompt on every path
    sections: dict[str, str] = {}
    for index, match in enumerate(matches):
        prefix, sep, suffix = match.group(1).strip().partition(":")
        key = f"{prefix.lower()}{sep}{suffix}"
        if key in sections:
            raise ValueError(f"task.md has duplicate section '## {key}': {path}")
        stop = matches[index + 1].start() if index + 1 < len(matches) else len(body)
        sections[key] = body[match.end():stop].strip()
    instruction = sections.get("prompt", body[: matches[0].start()].strip())
    return _ESCAPED_SECTION_RE.sub(r"\1", instruction)


def _reject_authoring_shorthands(data: dict[str, Any], path: Path) -> None:
    """Refuse five BenchFlow frontmatter shorthands by name.

    BenchFlow expands ``verifier: <path>``, ``oracle: <path>``, ``image:`` and
    ``profile:``/``profiles:`` into the canonical tables (a grader directory,
    an image, presets for network, workdir and timeouts); reading past them
    would grade the task under other rules than BenchFlow's. Only these five
    are refused. Other keys BenchFlow acts on (``agent.prompt_prefix``,
    multi-role ``agents``/``scenes``/``user``, ``verifier.type``,
    ``solution:``, ``sandbox.image``, ...) are still read past without an
    error; checking the whole frontmatter against BenchFlow's ``TaskConfig``
    is a follow-up.
    """

    for key in ("verifier", "oracle"):
        if data.get(key) is not None and not isinstance(data[key], dict):
            raise ValueError(
                f"task.md '{key}: <path>' shorthand is not supported; use a "
                f"'{key}' table and the conventional '{key}/' directory: {path}"
            )
    for key in ("image", "profile", "profiles"):
        if key in data:
            raise ValueError(
                f"task.md '{key}:' shorthand is not supported; write the "
                f"canonical sandbox/agent/verifier tables instead: {path}"
            )


def _plain(value: Any) -> Any:
    """Make a parsed config JSON-serialisable, whichever layout it came from.

    YAML turns an unquoted ``2024-01-02`` into a ``date`` (also as a mapping
    key) and ``!!set`` into a ``set``; TOML has date, time and datetime
    values. The run artifact's ``json.dumps`` rejects all of them, after every
    cell has already run. Dates and times become ISO 8601 strings (a datetime
    as ``2024-01-02T10:30:00``), keys are converted the same way, and sets
    become sorted lists.
    """
    if isinstance(value, dict):
        return {_plain(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    if isinstance(value, (set, frozenset)):
        return sorted((_plain(v) for v in value), key=str)
    if isinstance(value, (datetime.date, datetime.time)):  # datetime is a date
        return value.isoformat()
    return value


def _first_dir(root: Path, names: tuple[str, ...]) -> Path:
    for name in names:
        if (root / name).is_dir():
            return root / name
    # Neither exists: name the format's primary directory so the caller's
    # error message points at what the task should have shipped.
    return root / names[0]


def _table(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


# Docker-harness defaults for a task that does not set its own.
_DEFAULT_BUILD_TIMEOUT = 600.0
_DEFAULT_VERIFY_TIMEOUT = 180.0


def _is_public(task: TaskSpec) -> bool:
    return str(task.environment.get("network_mode") or "none") == "public"


def _task_build_timeout(task: TaskSpec) -> float:
    """Image build timeout: the task's ``environment.build_timeout_sec`` if set,
    else :data:`_DEFAULT_BUILD_TIMEOUT`."""
    val = task.environment.get("build_timeout_sec")
    try:
        return float(val) if val is not None else _DEFAULT_BUILD_TIMEOUT
    except (TypeError, ValueError):
        return _DEFAULT_BUILD_TIMEOUT


def _task_verify_timeout(task: TaskSpec) -> float:
    """Verifier timeout: the task's ``verifier.timeout_sec`` if set, else
    :data:`_DEFAULT_VERIFY_TIMEOUT`."""
    val = task.verifier.get("timeout_sec")
    try:
        return float(val) if val is not None else _DEFAULT_VERIFY_TIMEOUT
    except (TypeError, ValueError):
        return _DEFAULT_VERIFY_TIMEOUT
