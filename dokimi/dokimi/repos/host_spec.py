# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The ``host`` block of a task.json: parsed and checked, nothing else.

A leaf module (it imports no other repos module but ``errors``), so ``manifest`` can validate
the block without the cycle through ``host`` -> ``workspace`` -> ``manifest``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .errors import PrepError

HOST_KEYS = ("min_cpus", "cpu_vendors", "cpu_flags")

# /proc/cpuinfo vendor_id strings of x86 CPUs; compared in any case. arm64 cpuinfo has no vendor_id.
KNOWN_VENDORS = ("GenuineIntel", "AuthenticAMD", "HygonGenuine", "CentaurHauls", "Shanghai")


class HostSpecError(PrepError):
    """A ``task.json`` ``host`` value of the wrong shape."""


@dataclass(frozen=True)
class HostRequirements:
    min_cpus: int = 1
    cpu_vendors: tuple[str, ...] = ()
    cpu_flags: tuple[str, ...] = ()

    def describe(self) -> str:
        parts = [f"min_cpus {self.min_cpus}"] if self.min_cpus > 1 else []
        if self.cpu_vendors:
            parts.append("cpu_vendors " + "|".join(self.cpu_vendors))
        if self.cpu_flags:
            parts.append("cpu_flags " + ",".join(self.cpu_flags))
        return ", ".join(parts) or "nothing"


def parse_host(raw: Any, task_id: str) -> HostRequirements | None:
    """The ``host`` value of a task.json as requirements, or ``None`` without any (an empty
    object is none too); :class:`HostSpecError` on a bad shape."""
    if raw is None:
        return None
    where = f"task {task_id}: task.json host"
    if not isinstance(raw, dict):
        raise HostSpecError(f"{where} must be an object with optional keys {', '.join(HOST_KEYS)}, "
                            f"got {type(raw).__name__}")
    unknown = sorted(set(raw) - set(HOST_KEYS))
    if unknown:
        raise HostSpecError(f"{where}: unknown key(s) {', '.join(map(str, unknown))} "
                            f"(known: {', '.join(HOST_KEYS)})")
    min_cpus = raw.get("min_cpus", 1)
    if isinstance(min_cpus, bool) or not isinstance(min_cpus, int) or min_cpus < 1:
        raise HostSpecError(f"{where}.min_cpus must be an integer >= 1, got {min_cpus!r}")
    vendors = _names(raw, "cpu_vendors", where)
    unknown_vendors = [v for v in vendors if v.lower() not in {k.lower() for k in KNOWN_VENDORS}]
    if unknown_vendors:
        raise HostSpecError(f"{where}.cpu_vendors: unknown vendor(s) {', '.join(unknown_vendors)} "
                            f"(/proc/cpuinfo vendor_id, one of {', '.join(KNOWN_VENDORS)}; any case)")
    need = HostRequirements(min_cpus, vendors, _names(raw, "cpu_flags", where))
    return None if need == HostRequirements() else need  # no constraint: a probe failure cannot unsupport it


def _names(raw: dict[str, Any], key: str, where: str) -> tuple[str, ...]:
    value = raw.get(key, [])
    if not isinstance(value, list) or not all(isinstance(v, str) and v.strip() for v in value):
        raise HostSpecError(f"{where}.{key} must be a list of non-empty strings (/proc/cpuinfo names), "
                            f"got {value!r}")
    return tuple(v.strip() for v in value)
