# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Harness profiles (harness_profile.v1): how to invoke, isolate and read one agent CLI.

A profile is data, not code. ``invoke`` is an argv template, and every element
becomes exactly one argument after placeholder substitution, so a prompt with
quotes or spaces never passes through a shell. The element ``"{tools}"`` is
replaced by the argument list of the selected tool mode. The child process
gets a scrubbed environment: only ``env_allow`` names are inherited, then
``env_set`` is applied, then ``config_dir_env`` points at a per-run directory
so the user's own settings and memory cannot leak into a run.
"""

from __future__ import annotations

import os
import re
import signal
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from dokimi.artifacts import schema_errors

PROFILE_DIR = Path(__file__).resolve().parent / "profiles"
PLACEHOLDERS = frozenset({"bin", "prompt", "model", "max_turns", "budget"})
CONFIG_PLACEHOLDERS = frozenset({"config_dir", "support_dir"})
TOOLS_ELEMENT = "{tools}"
_PLACEHOLDER_RE = re.compile(r"\{([a-z_]+)\}")
_VERSION_RE = re.compile(r"\d+\.\d+\.\d+")

if sys.platform == "win32":
    import ctypes

    _kernel32 = ctypes.WinDLL("kernel32")
    _kernel32.CreateJobObjectW.restype = ctypes.c_void_p
    _kernel32.AssignProcessToJobObject.argtypes = (ctypes.c_void_p, ctypes.c_void_p)
    _kernel32.TerminateJobObject.argtypes = (ctypes.c_void_p, ctypes.c_uint)
    _kernel32.CloseHandle.argtypes = (ctypes.c_void_p,)


class ProfileError(ValueError):
    """A harness profile that cannot be loaded or used."""


@dataclass(frozen=True)
class HarnessProfile:
    name: str
    bin: str
    version_cmd: tuple[str, ...]
    invoke: tuple[str, ...]
    tools: dict[str, tuple[str, ...]]
    trace: str
    config_dir_env: str | None = None
    env_allow: tuple[str, ...] = ()
    env_set: dict[str, str] = field(default_factory=dict)
    egress: tuple[str, ...] = ()
    endpoint_env: str | None = None
    guidance: dict[str, Any] = field(default_factory=dict)
    bridge: dict[str, str] | None = None
    prompt_suffix: str = ""
    config_files: dict[str, str] = field(default_factory=dict)
    path: str = "<profile>"

    @property
    def guidance_names(self) -> tuple[str, ...]:
        """File names the harness is declared to load (verified only by the reach probe)."""
        return tuple(self.guidance.get("names") or ())

    def argv(
        self,
        *,
        prompt: str,
        model: str,
        tools: str = "none",
        max_turns: int = 4,
        budget_usd: float = 0.25,
        bin_path: str | None = None,
    ) -> list[str]:
        """The command line for one run, without a shell."""
        if tools not in self.tools:
            raise ProfileError(f"{self.path}: tool mode {tools!r} is not defined "
                               f"(have: {', '.join(sorted(self.tools))})")
        values = {
            "bin": bin_path or self.bin,
            "prompt": prompt,
            "model": model,
            "max_turns": str(max_turns),
            "budget": f"{budget_usd:g}",
        }
        out: list[str] = []
        for element in self.invoke:
            if element == TOOLS_ELEMENT:
                out.extend(self.tools[tools])
            else:
                out.append(_substitute(element, values))
        return out

    def prompt(self, task_prompt: str) -> str:
        """The task prompt as the agent gets it: with ``prompt_suffix`` after a blank line."""
        return f"{task_prompt}\n\n{self.prompt_suffix}" if self.prompt_suffix else task_prompt

    def write_config(self, config_dir: Path, values: dict[str, str]) -> list[str]:
        """Write ``config_files`` into ``config_dir``; ``values`` fills {config_dir} and {support_dir}."""
        config_dir.mkdir(parents=True, exist_ok=True)
        for name, content in self.config_files.items():
            (config_dir / name).write_text(_substitute(content, values), encoding="utf-8")
        return sorted(self.config_files)

    def version_argv(self, bin_path: str | None = None) -> list[str]:
        values = {"bin": bin_path or self.bin}
        return [_substitute(element, values) for element in self.version_cmd]

    def env(self, config_dir: Path, base: dict[str, str] | None = None) -> dict[str, str]:
        """The child environment: allow-listed names, then env_set, then the config dir."""
        source = os.environ if base is None else base
        env = {name: source[name] for name in self.env_allow if name in source}
        env.update(self.env_set)
        if self.config_dir_env:
            env[self.config_dir_env] = str(config_dir)
        return env

    def version(self, env: dict[str, str], bin_path: str | None = None, timeout: int = 60) -> str:
        """Run ``version_cmd`` and return the first x.y.z it prints (or the raw output)."""
        argv = self.version_argv(bin_path)
        try:
            proc = run_harness(argv, env=env, timeout=timeout)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ProfileError(f"{self.name}: cannot run {argv[0]!r}: {exc}") from exc
        text = (proc.stdout or proc.stderr).strip()
        if proc.returncode != 0:
            raise ProfileError(f"{self.name}: {' '.join(argv)} exited {proc.returncode}: {text[:200]}")
        match = _VERSION_RE.search(text)
        return match.group(0) if match else text.splitlines()[0] if text else "unknown"


def run_harness(argv: list[str], *, timeout: float, **kwargs: Any) -> subprocess.CompletedProcess[str]:
    """subprocess.run with captured UTF-8 output, except that a timeout or an interrupt kills the
    harness's process group (POSIX) or job object (Windows), not only the direct child: a helper
    the harness started would keep the pipes open or outlive the run with its credentials.

    A process that leaves the group (``setsid()``, a daemon) is not contained: it can outlive the
    run. On POSIX the call then does not wait for it to close the pipes, since the output of a
    killed run is discarded; Windows waits up to 10 s, which only a process outside the job uses."""
    proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                            encoding="utf-8", errors="replace",
                            start_new_session=sys.platform != "win32", **kwargs)
    job = None
    if sys.platform == "win32":  # taskkill /T misses a helper whose parent already exited
        job = _kernel32.CreateJobObjectW(None, None)
        if job and not _kernel32.AssignProcessToJobObject(job, int(proc._handle)):  # type: ignore[attr-defined]
            _kernel32.CloseHandle(job)
            job = None
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except BaseException:
        _kill_tree(proc, job)
        try:
            if sys.platform == "win32":
                proc.communicate(timeout=10)  # joins the reader threads
            else:
                proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pass
        if sys.platform != "win32":  # a process that left the group may hold the write ends
            for pipe in (proc.stdout, proc.stderr):
                if pipe:
                    pipe.close()
        raise
    finally:
        if sys.platform == "win32" and job:
            _kernel32.CloseHandle(job)
    return subprocess.CompletedProcess(argv, proc.returncode, stdout, stderr)


def _kill_tree(proc: subprocess.Popen[str], job: int | None) -> None:
    if sys.platform == "win32" and job:
        _kernel32.TerminateJobObject(job, 1)
    elif sys.platform == "win32":
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True)
    else:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except OSError:
            pass
    proc.kill()


def builtin_profiles() -> list[str]:
    return sorted(p.stem for p in PROFILE_DIR.glob("*.yaml"))


def load_profile(name_or_path: str | Path) -> HarnessProfile:
    """Load a built-in profile by name, or a profile YAML file by path."""
    text = str(name_or_path)
    path = Path(text)
    if not path.is_file():
        builtin = PROFILE_DIR / f"{text}.yaml"
        if "/" in text or not builtin.is_file():
            raise ProfileError(f"no harness profile {text!r} (built-in: {', '.join(builtin_profiles())})")
        path = builtin
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ProfileError(f"{path}: not valid YAML: {exc}") from exc
    return parse_profile(raw, str(path))


def parse_profile(raw: Any, path: str = "<profile>") -> HarnessProfile:
    errors = schema_errors("harness_profile.v1", raw)
    if errors:
        raise ProfileError(f"{path}: " + "; ".join(errors))
    for key in ("invoke", "version_cmd"):
        for element in raw[key]:
            if element == TOOLS_ELEMENT:
                if key == "version_cmd":
                    raise ProfileError(f"{path}: version_cmd cannot use {TOOLS_ELEMENT}")
                continue
            unknown = set(_PLACEHOLDER_RE.findall(element)) - (PLACEHOLDERS if key == "invoke" else {"bin"})
            if unknown:
                raise ProfileError(f"{path}: {key} uses unknown placeholder(s) {sorted(unknown)}")
    for name, content in (raw.get("config_files") or {}).items():
        unknown = set(_PLACEHOLDER_RE.findall(content)) - CONFIG_PLACEHOLDERS
        if unknown:
            raise ProfileError(f"{path}: config_files/{name} uses unknown placeholder(s) {sorted(unknown)}")
    if not any("{prompt}" in element for element in raw["invoke"]):
        raise ProfileError(f"{path}: invoke never passes {{prompt}}")
    if raw["invoke"].count(TOOLS_ELEMENT) != 1:
        raise ProfileError(f"{path}: invoke must have exactly one {TOOLS_ELEMENT} element")
    return HarnessProfile(
        name=raw["name"],
        bin=raw["bin"],
        version_cmd=tuple(raw["version_cmd"]),
        invoke=tuple(raw["invoke"]),
        tools={mode: tuple(args) for mode, args in raw["tools"].items()},
        trace=raw["trace"],
        config_dir_env=raw.get("config_dir_env"),
        env_allow=tuple(raw.get("env_allow") or ()),
        env_set=dict(raw.get("env_set") or {}),
        egress=tuple(raw.get("egress") or ()),
        endpoint_env=raw.get("endpoint_env"),
        guidance=dict(raw.get("guidance") or {}),
        bridge=raw.get("bridge"),
        prompt_suffix=raw.get("prompt_suffix") or "",
        config_files=dict(raw.get("config_files") or {}),
        path=path,
    )


def _substitute(element: str, values: dict[str, str]) -> str:
    return _PLACEHOLDER_RE.sub(lambda m: values.get(m.group(1), m.group(0)), element)
