# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Task host requirements: ``task.json`` ``host`` (contract §3, §4.3).

Some hidden tests can only see their bug on some hardware: a thread race that
needs three or more CPUs, an ISA dispatch bug that needs ``avx512f``. Elsewhere
the unfixed tree passes, so a grade there would be a false pass, and a
self-check there a false "grader broken". Such a task declares::

    "host": {"min_cpus": 4, "cpu_vendors": ["GenuineIntel"], "cpu_flags": ["avx2"]}

(every key optional). Vendor and flag names are ``/proc/cpuinfo``'s.

The requirement is checked against what a container of the evaluation sees,
not the host: :func:`probe_host` runs a stdlib Python probe in a container
started like the grading container (same image, same runner flags). Its CPU
count is the affinity mask capped by the cgroup CPU quota (``docker --cpus``),
which is what a threading runtime such as oneTBB sizes itself by. The probe
runs once per ``repo run`` or ``repo tasks check`` invocation; its facts are
recorded with the batch and in each cell's ``meta.json``.
"""

from __future__ import annotations

import json
import math
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .container import ContainerError, ContainerSpec
from .grading import EvalContext
from .workspace import PrepError, Task

HOST_KEYS = ("min_cpus", "cpu_vendors", "cpu_flags")
PROBE_TIMEOUT_S = 120

# Runs in the image: stdlib only, reads files and prints one JSON object. The host interprets it.
PROBE = r"""
import json, os
def read(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return None
def resolve_cgroup_limits():
    # Resolve CPU limits via /proc/self/cgroup and /proc/self/mountinfo, accounting for cgroup namespace.
    cgroup_text = read("/proc/self/cgroup") or ""
    mountinfo_text = read("/proc/self/mountinfo") or ""
    cpu_max, cfs_quota_us, cfs_period_us = None, None, None
    # Try v2 first (unified hierarchy): find the cgroup path from cgroup v2 entry
    for line in cgroup_text.split("\n"):
        if line.startswith("0::"):
            parts = line.split("::")
            if len(parts) >= 3:
                cgroup_v2_path = parts[2]
                # Find v2 mount point from mountinfo
                for mline in mountinfo_text.split("\n"):
                    if "cgroup2" in mline:
                        mparts = mline.split()
                        if len(mparts) >= 5:
                            mount_point = mparts[4]
                            try:
                                full_path = f"{mount_point}{cgroup_v2_path}".rstrip("/")
                                cpu_max = read(f"{full_path}/cpu.max")
                                if cpu_max:
                                    return {"cpu_max": cpu_max, "cfs_quota_us": None, "cfs_period_us": None}
                            except (IndexError, OSError):
                                pass
    # Fall back to v1 paths for devices with only cgroup v1
    cpu_max = read("/sys/fs/cgroup/cpu.max")
    cfs_quota_us = read("/sys/fs/cgroup/cpu/cpu.cfs_quota_us")
    cfs_period_us = read("/sys/fs/cgroup/cpu/cpu.cfs_period_us")
    return {"cpu_max": cpu_max, "cfs_quota_us": cfs_quota_us, "cfs_period_us": cfs_period_us}
try:
    affinity = len(os.sched_getaffinity(0))
except AttributeError:
    affinity = os.cpu_count()
cpuinfo = read("/proc/cpuinfo") or ""
cgroup_limits = resolve_cgroup_limits()
print(json.dumps({**cgroup_limits, "affinity": affinity, "cpuinfo": cpuinfo.strip().split("\n\n")[0]}))
"""


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


def requirements(task: Task) -> HostRequirements | None:
    """The task's ``host`` requirements, or ``None`` without any; :class:`HostSpecError` on a bad shape."""
    raw = task.raw.get("host")
    if raw is None:
        return None
    where = f"task {task.id}: task.json host"
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
    return HostRequirements(min_cpus, _names(raw, "cpu_vendors", where), _names(raw, "cpu_flags", where))


def _names(raw: dict[str, Any], key: str, where: str) -> tuple[str, ...]:
    value = raw.get(key, [])
    if not isinstance(value, list) or not all(isinstance(v, str) and v.strip() for v in value):
        raise HostSpecError(f"{where}.{key} must be a list of non-empty strings (/proc/cpuinfo names), "
                            f"got {value!r}")
    return tuple(v.strip() for v in value)


def validate_tasks(tasks: list[Task]) -> None:
    """Refuse a bad ``host`` value before anything runs, not cell by cell."""
    for task in tasks:
        requirements(task)


def cgroup_cpus(raw: dict[str, Any]) -> int | None:
    """CPUs the cgroup quota allows (v2 ``cpu.max``, else v1 ``cfs_quota_us``), rounded up; ``None`` unlimited."""
    pairs = []
    if isinstance(raw.get("cpu_max"), str):
        pairs.append(tuple(raw["cpu_max"].split()[:2]))
    if isinstance(raw.get("cfs_quota_us"), str) and isinstance(raw.get("cfs_period_us"), str):
        pairs.append((raw["cfs_quota_us"].strip(), raw["cfs_period_us"].strip()))
    for pair in pairs:
        if len(pair) != 2:
            continue
        quota, period = pair
        if quota in ("max", "-1"):
            return None
        try:
            q, p = int(quota), int(period)
        except ValueError:
            continue
        if q > 0 and p > 0:
            return max(1, math.ceil(q / p))
    return None


def host_facts(raw: dict[str, Any]) -> dict[str, Any]:
    """The probe's output as facts: ``cpus`` (affinity capped by the quota), vendor, model and flags."""
    affinity = raw.get("affinity") if isinstance(raw.get("affinity"), int) else None
    quota = cgroup_cpus(raw)
    counts = [n for n in (affinity, quota) if n]
    text = str(raw["cpuinfo"]) if isinstance(raw.get("cpuinfo"), str) else ""
    flags = _field(text, "flags") or _field(text, "Features")  # x86 | arm64
    return {"cpus": min(counts) if counts else None, "affinity_cpus": affinity, "cgroup_cpus": quota,
            "cpu_vendor": _field(text, "vendor_id"), "cpu_model": _field(text, "model name"),
            "cpu_flags": sorted(set(flags.split())) if flags else []}


def _field(text: str, name: str) -> str | None:
    match = re.search(rf"^{re.escape(name)}\s*:\s*(.*)$", text, re.M)
    return match.group(1).strip() if match else None


def probe_host(ctx: EvalContext, work_dir: Path) -> dict[str, Any]:
    """Facts of a container like the grading one, or ``{"error": ...}`` when the probe could not run.

    The spec carries what decides the CPU view: the evaluation's image and runner (whose flags
    every container shares); no mounts, no network.
    """
    work_dir.mkdir(parents=True, exist_ok=True)
    out, err = work_dir / "host-probe.out", work_dir / "host-probe.err"
    # HOME is the probe container's own /tmp, never a host path.
    spec = ContainerSpec(image=ctx.image, argv=["python3", "-c", PROBE], workdir="/",
                         env={"HOME": "/tmp"}, network="none",  # nosec B108
                         name=f"dokimi-host-probe-{uuid.uuid4().hex[:8]}")
    try:
        result = ctx.runner.run(spec, stdout=out, stderr=err, timeout_s=PROBE_TIMEOUT_S)
    except ContainerError as exc:
        return {"error": f"host probe did not start: {str(exc)[:300]}"}
    text = out.read_text(encoding="utf-8", errors="replace") if out.is_file() else ""
    try:
        raw = json.loads(text.strip().splitlines()[-1]) if result.ok and text.strip() else None
    except ValueError:
        raw = None
    if not isinstance(raw, dict):
        tail = (err.read_text(encoding="utf-8", errors="replace") if err.is_file() else "").strip()[-300:]
        how = "timed out" if result.timed_out else f"exited {result.returncode}"
        return {"error": f"host probe {how} in {ctx.image} (python3 -c): {tail or text.strip()[-300:]}"}
    return host_facts(raw)


def unmet(task: Task, facts: dict[str, Any] | None) -> str | None:
    """Why the task cannot be graded meaningfully here, or ``None`` when it can (or needs nothing)."""
    need = requirements(task)
    if need is None:
        return None
    if not facts or facts.get("error") or facts.get("cpus") is None:
        why = (facts or {}).get("error") or "the host probe reported no CPU count"
        return f"needs {need.describe()}; cannot tell on this host: {why}"
    short = _shortfalls(need, facts)
    if not short:
        return None
    return (f"needs {need.describe()} to show its bug; the grading container has {'; '.join(short)} "
            f"({_describe(facts)})")


def _shortfalls(need: HostRequirements, facts: dict[str, Any]) -> list[str]:
    short = []
    if facts["cpus"] < need.min_cpus:
        short.append(f"{facts['cpus']} CPU(s) < min_cpus {need.min_cpus}")
    if need.cpu_vendors and facts.get("cpu_vendor") not in need.cpu_vendors:
        short.append(f"vendor {facts.get('cpu_vendor')} not in {', '.join(need.cpu_vendors)}")
    missing = [f for f in need.cpu_flags if f not in (facts.get("cpu_flags") or [])]
    if missing:
        short.append(f"missing cpu_flags {', '.join(missing)}")
    return short


def _describe(facts: dict[str, Any]) -> str:
    quota = facts.get("cgroup_cpus")
    return (f"affinity {facts.get('affinity_cpus')}, cgroup quota {quota if quota else 'none'}; "
            f"{facts.get('cpu_model')} ({facts.get('cpu_vendor')})")
