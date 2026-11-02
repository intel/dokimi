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
runs once per ``repo run`` or ``eval check`` invocation; its facts are
recorded with the batch and in each cell's ``meta.json``.
"""

from __future__ import annotations

import json
import math
import re
import uuid
from pathlib import Path
from typing import Any

from .container import ContainerError, ContainerSpec
from .grading import EvalContext
from .host_spec import HOST_KEYS, KNOWN_VENDORS, HostRequirements, HostSpecError, parse_host  # noqa: F401
from .workspace import Task

PROBE_TIMEOUT_S = 120

# Runs in the image: stdlib only, reads files and prints one JSON object. The host interprets it.
PROBE = r"""
import json, os
ROOT = os.environ.get("DOKIMI_PROBE_ROOT", "")  # tests point this at fake /proc and /sys trees
def read(path):
    try:
        with open(ROOT + path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return None
def inside(own, root):
    # The container's cgroup path as seen under a mount whose root (mountinfo field 4) is `root`:
    # a mount of /kubepods shows /kubepods/pod1 as /pod1.
    prefix = root.rstrip("/")
    if own == prefix or own.startswith(prefix + "/"):
        return own[len(prefix):]
    return own
def v2_cpu_max():
    # The container's own cgroup (/proc/self/cgroup, "0::<path>") under the cgroup2 mount
    # (/proc/self/mountinfo); every visible ancestor's cpu.max too. The tightest quota wins.
    own = None
    for line in (read("/proc/self/cgroup") or "").splitlines():
        if line.startswith("0::"):
            own = line.split(":", 2)[2]
    mounts = []
    for line in (read("/proc/self/mountinfo") or "").splitlines():
        fields = line.split()
        if "-" in fields and len(fields) > 4 and fields[fields.index("-") + 1] == "cgroup2":
            mounts.append((fields[3], fields[4]))
    if own is None or not mounts:
        return None
    root, mount = mounts[0]
    parts = [p for p in inside(own, root).split("/") if p]
    best, best_ratio = None, None
    for depth in range(len(parts), -1, -1):
        text = read(mount + "".join("/" + p for p in parts[:depth]) + "/cpu.max")
        if text is None:
            continue
        best = best or text
        fields = text.split()
        try:
            ratio = int(fields[0]) / int(fields[1])
        except (ValueError, IndexError):
            continue
        if best_ratio is None or ratio < best_ratio:
            best, best_ratio = text, ratio
    return best
def v1_cfs():
    # The same walk for cgroup v1: the cpu controller, possibly combined ("cpu,cpuacct"), under its
    # cgroup mount. Returns the (quota, period) texts of the tightest limit, or None.
    own = None
    for line in (read("/proc/self/cgroup") or "").splitlines():
        fields = line.split(":", 2)
        if len(fields) == 3 and "cpu" in fields[1].split(","):
            own = fields[2]
    mounts = []
    for line in (read("/proc/self/mountinfo") or "").splitlines():
        fields = line.split()
        if "-" in fields and len(fields) > 4:
            sep = fields.index("-")
            if fields[sep + 1] == "cgroup" and "cpu" in (fields[sep + 3] if len(fields) > sep + 3 else "").split(","):
                mounts.append((fields[3], fields[4]))
    if own is None or not mounts:
        return None
    root, mount = mounts[0]
    parts = [p for p in inside(own, root).split("/") if p]
    best = None
    for depth in range(len(parts), -1, -1):
        base = mount + "".join("/" + p for p in parts[:depth])
        quota, period = read(base + "/cpu.cfs_quota_us"), read(base + "/cpu.cfs_period_us")
        if quota is None or period is None:
            continue
        try:
            q, p = int(quota), int(period)
        except ValueError:
            continue
        if q > 0 and p > 0 and (best is None or q / p < best[0] / best[1]):
            best = (q, p, quota, period)  # -1 (unlimited) is neither a limit nor a candidate
    return (best[2], best[3]) if best else None
cpu_max = v2_cpu_max()
if cpu_max is None:
    cpu_max = read("/sys/fs/cgroup/cpu.max")
cfs = v1_cfs() or (read("/sys/fs/cgroup/cpu/cpu.cfs_quota_us"), read("/sys/fs/cgroup/cpu/cpu.cfs_period_us"))
try:
    affinity = len(os.sched_getaffinity(0))
except AttributeError:
    affinity = os.cpu_count()
cpuinfo = read("/proc/cpuinfo") or ""
print(json.dumps({"cpu_max": cpu_max, "cfs_quota_us": cfs[0], "cfs_period_us": cfs[1],
                  "affinity": affinity, "cpuinfo": cpuinfo.strip().split("\n\n")[0]}))
"""


def requirements(task: Task) -> HostRequirements | None:
    """The task's ``host`` requirements, or ``None`` without any; :class:`HostSpecError` on a bad shape."""
    return parse_host(task.raw.get("host"), task.id)


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


def unmet(task: Task, facts: dict[str, Any] | None) -> tuple[str | None, str | None]:
    """Why the task cannot be graded here; ``(reason, unsupported_by)`` or ``(None, None)`` when it can.

    ``unsupported_by`` is ``"host"`` for a hardware miss (e.g., insufficient CPUs), or
    ``"host-probe"`` for a probe failure (e.g., python3 not in image). A task with
    requirements and a broken probe cannot be verified, even with --allow-unsupported.
    """
    need = requirements(task)
    if need is None:
        return None, None
    if not facts or facts.get("cpus") is None:
        # A probe failure (error set, or no CPU count). Mark as "host-probe" so
        # --allow-unsupported does not hide a broken measurement.
        why = (facts or {}).get("error") or "the host probe reported no CPU count"
        return (f"needs {need.describe()}; cannot tell on this host: {why}", "host-probe")
    if (need.cpu_vendors or need.cpu_flags) and not facts.get("cpu_vendor") and not facts.get("cpu_flags"):
        # /proc/cpuinfo was unreadable (or empty): neither vendor nor flags were seen, so a miss is a guess.
        return (f"needs {need.describe()}; cannot tell on this host: the probe read no vendor or flags "
                f"from /proc/cpuinfo", "host-probe")
    short = [text for _, text in _shortfalls(need, facts)]
    if not short:
        return None, None
    reason = (f"needs {need.describe()} to show its bug; the grading container has {'; '.join(short)} "
              f"({_describe(facts)})")
    return reason, "host"


def shortfall_kinds(task: Task, facts: dict[str, Any] | None) -> set[str]:
    """Which requirements a measured host misses: any of ``cpus``, ``vendor``, ``flags``."""
    need = requirements(task)
    if need is None or not facts or facts.get("cpus") is None:
        return set()
    return {kind for kind, _ in _shortfalls(need, facts)}


def _shortfalls(need: HostRequirements, facts: dict[str, Any]) -> list[tuple[str, str]]:
    short = []
    if facts["cpus"] < need.min_cpus:
        short.append(("cpus", f"{facts['cpus']} CPU(s) < min_cpus {need.min_cpus}"))
    if need.cpu_vendors and (facts.get("cpu_vendor") or "").lower() not in {v.lower() for v in need.cpu_vendors}:
        short.append(("vendor", f"vendor {facts.get('cpu_vendor')} not in {', '.join(need.cpu_vendors)}"))
    have = {f.lower() for f in facts.get("cpu_flags") or []}  # docs write AVX512F, cpuinfo avx512f
    missing = [f for f in need.cpu_flags if f.lower() not in have]
    if missing:
        short.append(("flags", f"missing cpu_flags {', '.join(missing)}"))
    return short


def _describe(facts: dict[str, Any]) -> str:
    quota = facts.get("cgroup_cpus")
    return (f"affinity {facts.get('affinity_cpus')}, cgroup quota {quota if quota else 'none'}; "
            f"{facts.get('cpu_model')} ({facts.get('cpu_vendor')})")
