# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Harness profiles (harness_profile.v1): how to invoke, isolate and read one agent CLI.

A profile is data, not code. ``invoke`` is an argv template, and every element
becomes exactly one argument after placeholder substitution, so a prompt with
quotes or spaces never passes through a shell. The element ``"{tools}"`` is
replaced by the argument list of the selected tool mode, or by its
``delegation`` override when subagents are turned off. The child process
gets a scrubbed environment: only ``env_allow`` names are inherited, then
``env_set`` is applied, then ``config_dir_env`` points at a per-run directory
so the user's own settings and memory cannot leak into a run.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from dokimi import process
from dokimi.artifacts import schema_errors

PROFILE_DIR = Path(__file__).resolve().parent / "profiles"
PLACEHOLDERS = frozenset({"bin", "prompt", "model", "max_turns", "budget"})
CONFIG_PLACEHOLDERS = frozenset({"config_dir", "support_dir"})
TOOLS_ELEMENT = "{tools}"
DELEGATION_MODES = ("native", "none")  # native: the agent may start subagents; none: it cannot
_PLACEHOLDER_RE = re.compile(r"\{([a-z_]+)\}")
_VERSION_RE = re.compile(r"\d+\.\d+\.\d+")

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
    delegation: dict[str, dict[str, tuple[str, ...]]] = field(default_factory=dict)
    path: str = "<profile>"

    @property
    def guidance_names(self) -> tuple[str, ...]:
        """File names the harness is declared to load (verified only by the reach probe)."""
        return tuple(self.guidance.get("names") or ())

    @property
    def import_max_depth(self) -> int | None:
        """Hops the harness follows ``@`` imports (``guidance.import_max_depth``); None: unlimited."""
        depth = self.guidance.get("import_max_depth")
        return int(depth) if depth is not None else None

    def argv(
        self,
        *,
        prompt: str,
        model: str,
        tools: str = "none",
        max_turns: int = 4,
        budget_usd: float = 0.25,
        bin_path: str | None = None,
        delegation: str = "native",
    ) -> list[str]:
        """The command line for one run, without a shell."""
        tool_args = self.tool_args(tools, delegation)
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
                out.extend(tool_args)
            else:
                out.append(_substitute(element, values))
        return out

    def tool_args(self, tools: str, delegation: str = "native") -> tuple[str, ...]:
        """What ``{tools}`` becomes: the tool mode's arguments, or its ``delegation`` override."""
        if tools not in self.tools:
            raise ProfileError(f"{self.path}: tool mode {tools!r} is not defined "
                               f"(have: {', '.join(sorted(self.tools))})")
        if delegation == "native":
            return self.tools[tools]
        if delegation not in DELEGATION_MODES:
            raise ProfileError(f"unknown delegation mode {delegation!r} (known: {', '.join(DELEGATION_MODES)})")
        if delegation not in self.delegation:
            raise ProfileError(f"{self.path}: declares no `delegation: {delegation}`, so a run cannot be "
                               "kept from starting subagents")
        return self.delegation[delegation].get(tools, self.tools[tools])

    def execution_id(self, tools: str, delegation: str = "native", harness_bin: str | None = None,
                     harness_version: str | None = None) -> str:
        """Short hash of how the agent is started: the ``invoke`` template with its tool arguments.

        Two runs with the same id ran the same command line up to the per-cell values (prompt, model,
        caps); ``repo run`` reuses a finished cell only when its id matches. Includes the harness
        binary name and version to detect changes to the effective harness executable.
        """
        key = {"profile": self.name, "invoke": list(self.invoke), "tools": list(self.tool_args(tools, delegation)),
               "env_set": dict(sorted(self.env_set.items())), "config_files": dict(sorted(self.config_files.items())),
               "prompt_suffix": self.prompt_suffix, "harness_bin": harness_bin, "harness_version": harness_version}
        return hashlib.sha256(json.dumps(key, sort_keys=True).encode("utf-8")).hexdigest()[:12]

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

    def env(self, config_dir: Path) -> process.Env:
        """The child environment: allow-listed names, then env_set, then the config dir."""
        values = dict(self.env_set)
        if self.config_dir_env:
            values[self.config_dir_env] = str(config_dir)
        return process.Env.only(self.env_allow, values)

    def version(self, env: process.Env, bin_path: str | None = None, timeout: int = 60,
                runner: process.Runner = process.run) -> str:
        """Run ``version_cmd`` and return the first x.y.z it prints (or the raw output)."""
        argv = self.version_argv(bin_path)
        try:
            proc = runner(argv, env=env, timeout=timeout)
        except OSError as exc:
            raise ProfileError(f"{self.name}: cannot run {argv[0]!r}: {exc}") from exc
        if proc.timed_out:
            raise ProfileError(f"{self.name}: cannot run {argv[0]!r}: timed out after {timeout}s")
        text = (proc.stdout or proc.stderr).strip()
        if proc.returncode != 0:
            raise ProfileError(f"{self.name}: {' '.join(argv)} exited {proc.returncode}: {text[:200]}")
        match = _VERSION_RE.search(text)
        return match.group(0) if match else text.splitlines()[0] if text else "unknown"


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
        delegation={mode: {t: tuple(args) for t, args in by_tools.items()}
                    for mode, by_tools in (raw.get("delegation") or {}).items()},
        path=path,
    )


def _substitute(element: str, values: dict[str, str]) -> str:
    return _PLACEHOLDER_RE.sub(lambda m: values.get(m.group(1), m.group(0)), element)
