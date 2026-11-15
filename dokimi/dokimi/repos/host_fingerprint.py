# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The host fingerprint: what the machine that runs dokimi is, one collector per field group.

Each collector returns a flat dict of JSON scalars. A collector that fails returns ``{"error": ...}``
and the others still run, so one unreadable file never costs the probe. Nothing that identifies a
person or a machine is read or recorded: no host name, user name, IP address, environment value or
absolute path (an error message is scrubbed of paths before it is kept). The Windows CPU model is
``platform.processor()``, which Windows fills from ``PROCESSOR_IDENTIFIER``. Spawns go through
:mod:`dokimi.process`, and only where the platform has the tool: nothing is shelled to on Windows
except the cross-platform ``--version`` calls and ``nvidia-smi`` when it is on the path.
"""

from __future__ import annotations

import os
import platform
import re
import shutil
import socket
import sysconfig
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from dokimi import process
from dokimi.process import Env, Runner

COLLECTORS_VERSION = 1
SPAWN_TIMEOUT_S = 10
# Only what a spawned tool needs from the environment: the binary's search path, and on Windows its system root.
_SPAWN_ENV = ("PATH", "SYSTEMROOT")
# The collectors whose values decide a host's identity (the id of `fingerprint_id`); network state is not one:
# a docker bridge that appears between two runs must not split a batch.
IDENTITY_COLLECTORS = ("os", "cpu", "memory", "gpu", "python", "tools", "virt")
TOOLS = ("git", "docker", "cmake", "gcc", "clang", "make")  # version is the first number on the first line of `--version`
PROXY_VARS = ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy")
# DMI strings of hypervisors, matched in lower case against the vendor and product name.
VM_MARKERS = (("vmware", "vmware"), ("virtualbox", "virtualbox"), ("innotek", "virtualbox"), ("qemu", "qemu"),
              ("kvm", "kvm"), ("xen", "xen"), ("parallels", "parallels"), ("virtual machine", "hyper-v"))
_PATH = re.compile(r"(?:/[\w.+@%-]+)+|[A-Za-z]:\\\S*")


def _read_text(path: str) -> str | None:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return None


def _windows_total_memory() -> int:
    """Physical memory of a Windows host, from the kernel's ``GlobalMemoryStatusEx`` (no shell)."""
    import ctypes  # Windows only: the windll attribute does not exist elsewhere

    class MemoryStatus(ctypes.Structure):
        _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong), ("total_phys", ctypes.c_ulonglong),
                    ("avail_phys", ctypes.c_ulonglong), ("total_page", ctypes.c_ulonglong),
                    ("avail_page", ctypes.c_ulonglong), ("total_virtual", ctypes.c_ulonglong),
                    ("avail_virtual", ctypes.c_ulonglong), ("avail_extended", ctypes.c_ulonglong)]

    status = MemoryStatus(length=ctypes.sizeof(MemoryStatus))
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))  # type: ignore[attr-defined]
    return int(status.total_phys)


@dataclass(frozen=True)
class Probe:
    """What the collectors read and run. Tests pass fakes; the defaults read this machine."""

    system: str = field(default_factory=platform.system)
    read: Callable[[str], str | None] = _read_text
    which: Callable[[str], str | None] = shutil.which
    run: Runner = process.run
    env: Mapping[str, str] = field(default_factory=lambda: dict(os.environ))  # read for booleans only
    windows_memory: Callable[[], int] = _windows_total_memory


def _spawn(probe: Probe, argv: list[str]) -> str | None:
    """stdout of a command that exits 0, else ``None``: a tool that fails is a fact about the host, not an error."""
    done = probe.run(argv, timeout=SPAWN_TIMEOUT_S, env=Env.only(_SPAWN_ENV))
    return done.stdout if done.ok else None


def scrub(text: str) -> str:
    """``text`` with every absolute path replaced: an error message may name a file under the user's home."""
    return _PATH.sub("<path>", text)[:300]


def cpuinfo_field(text: str, name: str) -> str | None:
    match = re.search(rf"^{re.escape(name)}\s*:\s*(.*)$", text, re.M)
    return match.group(1).strip() if match else None


def _os(probe: Probe) -> dict[str, Any]:
    out: dict[str, Any] = {"system": probe.system, "release": platform.release(), "machine": platform.machine()}
    if probe.system == "Linux":
        out["distro"] = _os_release(probe)
    elif probe.system == "Darwin":
        out["macos_version"] = platform.mac_ver()[0] or None
    return out


def _os_release(probe: Probe) -> str | None:
    text = probe.read("/etc/os-release") or probe.read("/usr/lib/os-release") or ""
    match = re.search(r'^PRETTY_NAME="?([^"\n]*)"?', text, re.M)
    return match.group(1) if match else None


def _cpu(probe: Probe) -> dict[str, Any]:
    """The machine's CPU (the container's view is in the probe's top-level keys, from its cgroup)."""
    out: dict[str, Any] = {"logical_cpus": os.cpu_count(), "vendor": None, "model": None, "flags": None}
    if probe.system == "Linux":
        text = probe.read("/proc/cpuinfo") or ""
        out.update(vendor=cpuinfo_field(text, "vendor_id"), model=cpuinfo_field(text, "model name"),
                   flags=cpuinfo_field(text, "flags") or cpuinfo_field(text, "Features"))
    elif probe.system == "Darwin":
        out.update(model=_sysctl(probe, "machdep.cpu.brand_string"), vendor=_sysctl(probe, "machdep.cpu.vendor"))
        features = _sysctl(probe, "machdep.cpu.features")
        out["flags"] = features.lower() if features else None
    elif probe.system == "Windows":
        out["model"] = platform.processor() or None  # the kernel's string names the vendor, read from it
        vendor = re.search(r"GenuineIntel|AuthenticAMD", out["model"] or "")
        out["vendor"] = vendor.group(0) if vendor else None
    return out


def _sysctl(probe: Probe, name: str) -> str | None:
    value = _spawn(probe, ["sysctl", "-n", name])
    return value.strip() if value and value.strip() else None


def _memory(probe: Probe) -> dict[str, Any]:
    if probe.system == "Linux":
        match = re.search(r"^MemTotal:\s+(\d+) kB", probe.read("/proc/meminfo") or "", re.M)
        return {"total_bytes": int(match.group(1)) * 1024} if match else {"error": "no MemTotal line in the meminfo file"}
    if probe.system == "Darwin":
        value = _sysctl(probe, "hw.memsize")
        return {"total_bytes": int(value)} if value and value.isdigit() else {"error": "sysctl hw.memsize gave no number"}
    if probe.system == "Windows":
        return {"total_bytes": probe.windows_memory()}
    return {"error": f"no memory collector for {probe.system}"}


def _gpu(probe: Probe) -> dict[str, Any]:
    """NVIDIA only: ``nvidia-smi`` is run when it is on the path; other GPUs report ``present`` false."""
    if probe.which("nvidia-smi") is None:
        return {"present": False}
    rows = _spawn(probe, ["nvidia-smi", "--query-gpu=name,driver_version", "--format=csv,noheader"])
    if rows is None:
        return {"present": True, "error": "nvidia-smi --query-gpu=name,driver_version failed"}
    table = [[cell.strip() for cell in line.split(",")] for line in rows.splitlines() if line.strip()]
    banner = _spawn(probe, ["nvidia-smi"]) or ""
    cuda = re.search(r"CUDA Version:\s*(\d+(?:\.\d+)+)", banner)
    first = table[0] if table else []
    return {"present": True, "count": len(table), "name": first[0] if first else None,
            "driver_version": first[1] if len(first) > 1 else None, "cuda_version": cuda.group(1) if cuda else None}


def _python(probe: Probe) -> dict[str, Any]:
    return {"implementation": platform.python_implementation(), "version": platform.python_version(),
            "free_threaded": bool(sysconfig.get_config_var("Py_GIL_DISABLED"))}


def _tools(probe: Probe) -> dict[str, str | None]:
    """Per tool: its version, ``present`` when it runs without a readable version, ``None`` when absent."""
    out: dict[str, str | None] = {}
    for name in TOOLS:
        if probe.which(name) is None:
            out[name] = None
            continue
        text = _spawn(probe, [name, "--version"]) or ""
        number = re.search(r"\d+(?:\.\d+)+", text.split("\n", 1)[0])
        out[name] = number.group(0) if number else "present"
    return out


def _network(probe: Probe) -> dict[str, Any]:
    """Counts and booleans only: no interface name, address or proxy value."""
    return {"interfaces": len(socket.if_nameindex()), "default_route": _default_route(probe),
            "proxy_env": any(probe.env.get(name) for name in PROXY_VARS)}


def _default_route(probe: Probe) -> bool | None:
    if probe.system != "Linux":
        return None
    table = probe.read("/proc/net/route")
    if table is None:
        return None
    return any(line.split()[1:2] == ["00000000"] for line in table.splitlines()[1:] if len(line.split()) > 1)


def _virt(probe: Probe) -> dict[str, Any]:
    kind = _container_kind(probe)
    vm, vendor = _vm(probe)
    return {"container": kind is not None, "container_kind": kind, "vm": vm, "vm_vendor": vendor}


def _container_kind(probe: Probe) -> str | None:
    """``docker``, ``podman``, ``kubernetes`` or ``lxc`` when this process runs in one; ``None`` otherwise or unknown."""
    if probe.system != "Linux":
        return None
    if probe.read("/.dockerenv") is not None:
        return "docker"
    if probe.read("/run/.containerenv") is not None:
        return "podman"
    cgroup = probe.read("/proc/self/cgroup") or ""
    for marker, kind in {"kubepods": "kubernetes", "libpod": "podman", "lxc": "lxc", "docker": "docker"}.items():
        if marker in cgroup:
            return kind
    return None


def _vm(probe: Probe) -> tuple[bool | None, str | None]:
    """``(is a VM, its hypervisor)``; ``None`` when this platform cannot tell."""
    if probe.system == "Darwin":
        value = _sysctl(probe, "kern.hv_vmm_present")
        return (None, None) if value is None else (value == "1", None)
    if probe.system != "Linux":
        return None, None
    dmi = " ".join(probe.read(f"/sys/class/dmi/id/{name}") or "" for name in ("sys_vendor", "product_name")).lower()
    for needle, name in VM_MARKERS:
        if needle in dmi:
            return True, name
    flags = cpuinfo_field(probe.read("/proc/cpuinfo") or "", "flags") or ""
    if "hypervisor" in flags.split():
        return True, None
    return (False if dmi.strip() else None), None


COLLECTORS: dict[str, Callable[[Probe], dict[str, Any]]] = {
    "os": _os, "cpu": _cpu, "memory": _memory, "gpu": _gpu, "python": _python,
    "tools": _tools, "network": _network, "virt": _virt,
}


def collect(probe: Probe | None = None) -> dict[str, Any]:
    """``{"collectors_version", "collectors": {name: flat dict}}``; a collector that raises is ``{"error": ...}``."""
    probe = probe or Probe()
    out: dict[str, Any] = {}
    for name, collector in COLLECTORS.items():
        try:
            out[name] = collector(probe)
        except Exception as exc:  # noqa: BLE001 - one collector failing must not fail the probe
            out[name] = {"error": scrub(f"{type(exc).__name__}: {exc}")}
    return {"collectors_version": COLLECTORS_VERSION, "collectors": out}
