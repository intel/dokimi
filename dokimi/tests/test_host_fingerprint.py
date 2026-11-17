# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The host fingerprint's collectors (contract §3): per platform, failure isolation, privacy.

No docker and no real tool is run: spawns go to a :class:`FakeRunner`, files come from a dict.
The privacy tests also run the collectors on this machine, with its real host and user names.
"""

from __future__ import annotations

import getpass
import json
import os
import platform
import re
import socket
import sys
from pathlib import Path

import pytest

from dokimi.process import FakeRunner, reply
from dokimi.repos import host_fingerprint as hf
from dokimi.repos.host import fingerprint_id, match_report, probe_host
from dokimi.repos.container import ContainerResult
from dokimi.repos.grading import EvalContext
from dokimi.repos.manifest import parse_manifest
from dokimi.repos.workspace import Task
from repo_eval_fixture import MANIFEST

INTEL_CPUINFO = ("processor\t: 0\nvendor_id\t: GenuineIntel\nmodel name\t: Intel(R) Xeon(R) Platinum 8480+\n"
                 "flags\t\t: fpu sse2 avx2 avx512f hypervisor\n")
ROUTE = ("Iface\tDestination\tGateway \tFlags\tRefCnt\tUse\tMetric\tMask\n"
         "eth0\t00000000\t0102A8C0\t0003\t0\t0\t100\t00000000\n")


def _files(**paths: str) -> dict[str, str]:
    """A fake filesystem: ``_files(**{"/proc/meminfo": ...})`` keys are paths, written with ``__`` for ``/``."""
    return {p.replace("__", "/"): text for p, text in paths.items()}


def _probe(system: str = "Linux", files: dict[str, str] | None = None, calls: dict | None = None, **kw):
    """A :class:`Probe` whose files are ``files`` and whose spawns answer ``calls[argv[0]]`` (or nothing)."""
    files = files or {}
    answers = calls or {}

    def handler(call):
        key = " ".join(call.argv)
        if key in answers:
            return reply(answers[key])
        return reply(returncode=1)

    runner = FakeRunner(handler)
    kw.setdefault("which", lambda name: None)
    return hf.Probe(system=system, read=lambda path: files.get(path), run=runner,
                    env=kw.pop("env", {}), **kw), runner


def _leaves(value):
    if isinstance(value, dict):
        for item in value.values():
            yield from _leaves(item)
    else:
        yield value


def _paths_in(text: str) -> list[str]:
    """Absolute-looking paths in a serialised record: a slash or drive letter at the start of a token."""
    return re.findall(r"(?:^|[\s\"'=(,])(/[\w.+@%-]|[A-Za-z]:\\)", text)


# ------------------------------------------------------------------ the registry


def test_each_collector_gives_a_flat_dict_of_json_scalars_and_the_version_is_recorded():
    probe, _ = _probe(files=_files(**{"/proc__cpuinfo": INTEL_CPUINFO, "/proc__meminfo": "MemTotal: 2048 kB\n",
                                      "/proc__net__route": ROUTE}))
    out = hf.collect(probe)
    assert out["collectors_version"] == hf.COLLECTORS_VERSION == 1
    assert sorted(out["collectors"]) == sorted(hf.COLLECTORS) == [
        "cpu", "gpu", "memory", "network", "os", "python", "tools", "virt"]
    for name, facts in out["collectors"].items():
        assert isinstance(facts, dict), name
        for item in facts.values():
            assert item is None or isinstance(item, (str, int, float, bool)), (name, item)
    assert out["collectors"]["memory"] == {"total_bytes": 2048 * 1024}
    assert out["collectors"]["cpu"]["vendor"] == "GenuineIntel"


def test_a_failing_collector_reports_its_error_and_the_others_still_run(monkeypatch):
    def broken(probe):
        raise OSError(f"[Errno 2] No such file or directory: '{Path.home()}/nvidia/smi'")

    monkeypatch.setitem(hf.COLLECTORS, "gpu", broken)
    probe, _ = _probe(files=_files(**{"/proc__meminfo": "MemTotal: 1024 kB\n"}))
    out = hf.collect(probe)
    assert out["collectors"]["gpu"]["error"].startswith("OSError: ")
    assert out["collectors"]["memory"] == {"total_bytes": 1024 * 1024}
    assert "nvidia" not in out["collectors"]["gpu"]["error"] and "<path>" in out["collectors"]["gpu"]["error"]


def test_probe_host_keeps_the_container_keys_and_adds_the_fingerprint(tmp_path):
    """The existing keys (cpus, cpu_vendor, ...) are unchanged; the fingerprint sits beside them."""
    ctx = EvalContext(manifest=parse_manifest(MANIFEST), git_dir=tmp_path, content_dir=tmp_path, image="img:1",
                      runner=_ContainerAnswer({"affinity": 8, "cpu_max": "max 100000\n", "cfs_quota_us": None,
                                               "cfs_period_us": None, "cpuinfo": INTEL_CPUINFO}))
    facts = probe_host(ctx, tmp_path / "w", probe=_probe()[0])
    assert {"cpus", "affinity_cpus", "cgroup_cpus", "cpu_vendor", "cpu_model", "cpu_flags"} <= set(facts)
    assert facts["cpus"] == 8 and facts["cpu_vendor"] == "GenuineIntel"
    assert facts["collectors_version"] == 1 and set(facts["collectors"]) == set(hf.COLLECTORS)


class _ContainerAnswer:
    """A runner of the probe container that prints ``answer`` as its JSON line."""

    def __init__(self, answer):
        self.answer = answer

    def run(self, spec, *, stdout, stderr, timeout_s):
        stdout.write_text(json.dumps(self.answer) + "\n", encoding="utf-8")
        return ContainerResult(returncode=0, timed_out=False, wall_s=0.0)


# ------------------------------------------------------------------ per platform


def test_linux_host_reads_its_files_and_spawns_nothing_it_does_not_need():
    probe, runner = _probe(files=_files(**{"/proc__cpuinfo": INTEL_CPUINFO, "/proc__meminfo": "MemTotal: 4 kB\n",
                                           "/etc__os-release": 'NAME="Ubuntu"\nPRETTY_NAME="Ubuntu 24.04 LTS"\n'}))
    out = hf.collect(probe)["collectors"]
    assert out["os"] == {"system": "Linux", "release": platform.release(), "machine": platform.machine(),
                         "distro": "Ubuntu 24.04 LTS"}
    assert out["cpu"]["flags"] == "fpu sse2 avx2 avx512f hypervisor"
    assert out["virt"] == {"container": False, "container_kind": None, "vm": True, "vm_vendor": None}
    assert runner.argvs() == []  # no tool on the path, so nothing ran


def test_macos_reads_sysctl_and_no_proc_file(tmp_path):
    answers = {"sysctl -n hw.memsize": "17179869184\n", "sysctl -n machdep.cpu.brand_string": "Apple M2\n",
               "sysctl -n kern.hv_vmm_present": "0\n"}
    probe, runner = _probe(system="Darwin", calls=answers, files=_files(**{"/proc__meminfo": "MemTotal: 1 kB\n"}))
    out = hf.collect(probe)["collectors"]
    assert out["memory"] == {"total_bytes": 17179869184}
    assert out["cpu"]["model"] == "Apple M2" and out["virt"]["vm"] is False
    assert out["os"]["system"] == "Darwin" and "distro" not in out["os"]
    assert {argv[0] for argv in runner.argvs()} == {"sysctl"}


def test_macos_without_a_number_from_sysctl_is_an_error_for_that_field_only():
    probe, _ = _probe(system="Darwin", calls={"sysctl -n hw.memsize": "not a number\n"})
    assert hf.collect(probe)["collectors"]["memory"] == {"error": "sysctl hw.memsize gave no number"}


def test_windows_spawns_only_cross_platform_calls_and_reads_no_proc():
    which = lambda name: rf"C:\tools\{name}.exe" if name in ("git", "nvidia-smi") else None  # noqa: E731
    calls = {"git --version": "git version 2.45.1.windows.1\n", "nvidia-smi --query-gpu=name,driver_version --format=csv,noheader":
             "NVIDIA RTX A4000, 551.23\n", "nvidia-smi": "CUDA Version: 12.4"}
    probe, runner = _probe(system="Windows", which=which, calls=calls, windows_memory=lambda: 16 * 2**30,
                           windows_cpu=lambda: ("Intel(R) Core(TM) i7", "GenuineIntel"),
                           files=_files(**{"/proc__meminfo": "MemTotal: 1 kB\n"}))
    out = hf.collect(probe)["collectors"]
    assert out["memory"] == {"total_bytes": 16 * 2**30}
    assert out["cpu"]["vendor"] == "GenuineIntel" and out["cpu"]["flags"] is None
    assert out["cpu"]["model"] == "Intel(R) Core(TM) i7"
    assert out["tools"]["git"] == "2.45.1" and out["tools"]["docker"] is None
    assert out["gpu"] == {"present": True, "count": 1, "name": "NVIDIA RTX A4000", "driver_version": "551.23",
                          "cuda_version": "12.4"}
    assert out["virt"] == {"container": False, "container_kind": None, "vm": None, "vm_vendor": None}
    assert {argv[0] for argv in runner.argvs()} <= {"git", "nvidia-smi"}  # never sysctl, lscpu, free, cat


def test_windows_cpu_model_never_reads_the_processor_identifier_environment_value(monkeypatch):
    monkeypatch.setenv("PROCESSOR_IDENTIFIER", "GenuineIntel workstation-jdoe")
    monkeypatch.setattr(platform, "processor", lambda: os.environ["PROCESSOR_IDENTIFIER"])  # CPython's Windows source
    probe, _ = _probe(system="Windows", windows_memory=lambda: 2**30, windows_cpu=lambda: (None, None))
    out = hf.collect(probe)
    assert "workstation-jdoe" not in json.dumps(out)
    assert out["collectors"]["cpu"]["model"] is None


@pytest.mark.skipif(sys.platform != "win32", reason="reads the registry on Windows only")
def test_windows_cpu_is_read_from_the_registry():
    model, vendor = hf.Probe(system="Windows").windows_cpu()
    assert model and vendor


@pytest.mark.skipif(sys.platform != "win32", reason="reads the kernel's memory status on Windows only")
def test_windows_memory_is_the_kernels_total():
    assert hf.Probe(system="Windows").windows_memory() > 0


# ------------------------------------------------------------------ gpu, virt, network, tools, python


def test_gpu_reports_driver_and_cuda_when_nvidia_smi_is_on_the_path():
    rows = "NVIDIA A100-SXM4-80GB, 550.54.14\nNVIDIA A100-SXM4-80GB, 550.54.14\n"
    calls = {"nvidia-smi --query-gpu=name,driver_version --format=csv,noheader": rows,
             "nvidia-smi": "| NVIDIA-SMI 550.54.14  Driver Version: 550.54.14  CUDA Version: 12.4 |"}
    probe, _ = _probe(calls=calls, which=lambda name: "/usr/bin/nvidia-smi" if name == "nvidia-smi" else None)
    assert hf.collect(probe)["collectors"]["gpu"] == {
        "present": True, "count": 2, "name": "NVIDIA A100-SXM4-80GB", "driver_version": "550.54.14",
        "cuda_version": "12.4"}


def test_gpu_without_nvidia_smi_is_absent_and_a_failing_query_is_an_error():
    assert hf.collect(_probe()[0])["collectors"]["gpu"] == {"present": False}
    probe, _ = _probe(which=lambda name: "/usr/bin/nvidia-smi")  # on the path, but the query exits 1
    assert hf.collect(probe)["collectors"]["gpu"] == {
        "present": True, "error": "nvidia-smi --query-gpu=name,driver_version failed"}


@pytest.mark.parametrize("files, expected", [
    ({"/.dockerenv": ""}, {"container": True, "container_kind": "docker"}),
    ({"/run__.containerenv": ""}, {"container": True, "container_kind": "podman"}),
    ({"/proc__self__cgroup": "0::/kubepods/burstable/pod1/abc\n"}, {"container": True, "container_kind": "kubernetes"}),
    ({"/proc__self__cgroup": "0::/init.scope\n"}, {"container": False, "container_kind": None}),
])
def test_virt_detects_a_container_from_its_markers(files, expected):
    probe, _ = _probe(files=_files(**files))
    virt = hf.collect(probe)["collectors"]["virt"]
    assert {key: virt[key] for key in expected} == expected


def test_virt_names_a_vm_from_dmi_or_the_hypervisor_flag():
    dmi = {"/sys__class__dmi__id__sys_vendor": "QEMU\n", "/sys__class__dmi__id__product_name": "Standard PC\n"}
    probe, _ = _probe(files=_files(**dmi))
    assert hf.collect(probe)["collectors"]["virt"]["vm_vendor"] == "qemu"
    probe, _ = _probe(files=_files(**{"/proc__cpuinfo": INTEL_CPUINFO}))  # `hypervisor` in the flags
    assert hf.collect(probe)["collectors"]["virt"]["vm"] is True
    probe, _ = _probe(files=_files(**{"/sys__class__dmi__id__sys_vendor": "Dell Inc.\n"}))
    assert hf.collect(probe)["collectors"]["virt"]["vm"] is False


def test_network_counts_and_booleans_and_never_an_address_or_a_proxy_value(monkeypatch):
    monkeypatch.setattr(socket, "if_nameindex", lambda: [(1, "lo"), (2, "eth0")])
    probe, _ = _probe(files=_files(**{"/proc__net__route": ROUTE}), env={"HTTPS_PROXY": "http://10.1.2.3:3128"})
    net = hf.collect(probe)["collectors"]["network"]
    assert net == {"interfaces": 2, "default_route": True, "proxy_env": True}
    assert "10.1.2.3" not in json.dumps(net)


def test_tools_record_the_version_number_and_no_path():
    probe, _ = _probe(which=lambda name: f"/usr/bin/{name}" if name in ("git", "gcc") else None,
                      calls={"git --version": "git version 2.43.0\n", "gcc --version": "gcc (GCC) 13.2.0\nCopyright"})
    tools = hf.collect(probe)["collectors"]["tools"]
    assert tools == {"git": "2.43.0", "docker": None, "cmake": None, "gcc": "13.2.0", "clang": None, "make": None}
    probe, _ = _probe(which=lambda name: f"/usr/bin/{name}" if name == "make" else None, calls={})
    assert hf.collect(probe)["collectors"]["tools"]["make"] == "present"  # on the path, its version not printed


def test_a_tool_that_cannot_be_started_keeps_the_versions_of_the_others():
    def handler(call):
        if call.argv[0] == "make":
            raise OSError("[Errno 2] No such file or directory: 'make'")  # `which` found it; the spawn fails
        return reply("git version 2.43.0\n") if " ".join(call.argv) == "git --version" else reply(returncode=1)

    probe = hf.Probe(system="Linux", read=lambda path: None, run=FakeRunner(handler), env={},
                     which=lambda name: f"/usr/bin/{name}" if name in ("git", "make") else None)
    tools = hf.collect(probe)["collectors"]["tools"]
    assert tools == {"git": "2.43.0", "docker": None, "cmake": None, "gcc": None, "clang": None, "make": "present"}


def test_python_names_the_implementation_and_not_the_executable():
    python = hf.collect(_probe()[0])["collectors"]["python"]
    assert python["version"] == platform.python_version() and isinstance(python["free_threaded"], bool)
    assert sys.executable not in json.dumps(python)


# ------------------------------------------------------------------ privacy


def test_privacy_real_host_and_user_names_and_addresses_never_appear(monkeypatch):
    monkeypatch.setattr(platform, "release", lambda: "5.15.167.4-microsoft-standard-WSL2")  # a WSL2 kernel release
    facts = hf.collect(hf.Probe())
    text = json.dumps(facts, sort_keys=True)
    for secret in {socket.gethostname(), getpass.getuser(), os.path.expanduser("~")}:
        if len(secret) >= 4:
            assert secret not in text, "a host or user name leaked into the fingerprint"
    # Versions (a four-part kernel release such as 5.15.167.4, a tool version) are dotted quads, not addresses.
    versions = {"os": ("release",), "python": ("version",), "tools": tuple(hf.TOOLS)}
    unversioned = {name: {k: v for k, v in c.items() if k not in versions.get(name, ())}
                   for name, c in facts["collectors"].items() if isinstance(c, dict)}
    assert not re.search(r"\b\d{1,3}(?:\.\d{1,3}){3}\b", json.dumps(unversioned)), "an IPv4 address leaked"
    assert _paths_in(text) == [], "an absolute path leaked into the fingerprint"


def test_privacy_monkeypatched_identities_and_paths_never_appear(monkeypatch, tmp_path):
    monkeypatch.setattr(platform, "node", lambda: "tower-7f3a")
    monkeypatch.setattr(socket, "gethostname", lambda: "tower-7f3a")
    monkeypatch.setattr(socket, "if_nameindex", lambda: [(1, "lo"), (2, "eth0")])
    monkeypatch.setattr(getpass, "getuser", lambda: "jdoe")
    monkeypatch.setenv("HOME", "/home/jdoe")
    files = _files(**{"/proc__net__route": ROUTE.replace("eth0", "/home/jdoe/eth0"),
                      "/proc__cpuinfo": INTEL_CPUINFO, "/etc__os-release": 'PRETTY_NAME="Ubuntu 24.04 LTS"\n',
                      "/sys__class__dmi__id__sys_vendor": "Lenovo at 10.20.30.40\n"})
    calls = {"nvidia-smi --query-gpu=name,driver_version --format=csv,noheader": "A, 1\n", "nvidia-smi": "CUDA Version: 12.4"}
    probe, _ = _probe(files=files, calls=calls, env={"HOME": "/home/jdoe", "HTTP_PROXY": "http://10.20.30.40:3128"},
                       which=lambda name: f"/home/jdoe/bin/{name}" if name == "nvidia-smi" else None)
    monkeypatch.setattr(hf, "COLLECTORS", {**hf.COLLECTORS, "tools": lambda p: 1 / 0})  # a failing collector's text
    text = json.dumps(hf.collect(probe), sort_keys=True)
    for leak in ("tower-7f3a", "jdoe", "10.20.30.40", "/home", str(tmp_path)):
        assert leak not in text, leak
    assert "ZeroDivisionError: division by zero" in text and _paths_in(text) == []


def test_error_text_is_scrubbed_of_paths_and_truncated():
    assert hf.scrub("missing '/home/jdoe/.cache/x' and C:\\Users\\jdoe\\y") == "missing '<path>' and <path>"
    assert len(hf.scrub("x" * 1000)) == 300


@pytest.mark.parametrize("text, kept", [
    ("missing '/home/Jane Doe/private'", "missing '<path>'"),
    ("missing 'C:\\Users\\Jane Doe\\AppData\\x'", "missing '<path>'"),
    ("cannot open \\\\server\\users\\jdoe\\file", "cannot open <path>"),
    ("cannot open '\\\\server\\users\\Jane Doe\\file'", "cannot open '<path>'"),
    ("no such file ~/secret/x", "no such file <path>"),
    ("no such file '~/Jane Doe/x'", "no such file '<path>'"),
    ("missing \"/home/Jane O'Connor/private\"", "missing \"<path>\""),
    ("missing C:\\Users\\Jane Doe\\AppData\\x", "missing <path>"),
    ("missing C:\\Users\\O'Connor\\private.txt", "missing <path>"),
    ("cannot open \\\\server\\users\\O'Connor\\file", "cannot open <path>"),
    ("cannot open path:/home/jdoe/private", "cannot open path:<path>"),
    ("cannot open /home/Jane Doe/private", "cannot open <path>"),
    ("cannot open /home/Jane Doe/private: Permission denied", "cannot open <path>"),
    ("cannot open /home/张三/private", "cannot open <path>"),
    ("no such file ~/Jane Doe/x", "no such file <path>"),
    ("cannot open file:///home/Jane Doe/private", "cannot open file://<path>"),
    ("cannot open file:/home/Jane Doe/private", "cannot open file:<path>"),
    ("cannot open FILE:///home/jdoe/x", "cannot open FILE://<path>"),
    ("cannot open file://server/share/Jane Doe/x", "cannot open file:<path>"),
    ("missing \"file:///home/Jane O'Connor/x\", next", "missing \"<path>\", next"),
    ("cannot open //server/share/Jane Doe/file", "cannot open <path>"),
    ("cannot open '//server/share/Jane Doe/file'", "cannot open '<path>'"),
    ("see https://example.com/a b", "see https://example.com/a b"),
    ("see http://example.com/a/b c", "see http://example.com/a/b c"),
    ("host probe exited 1 in ghcr.io/intel/dokimi-grader:1 (python3 -c): boom",
     "host probe exited 1 in ghcr.io/intel/dokimi-grader:1 (python3 -c): boom"),
])
def test_scrub_redacts_quoted_unc_and_home_relative_paths_whole(text, kept):
    assert hf.scrub(text) == kept


def test_a_collector_error_naming_a_path_with_a_space_is_stored_without_it(monkeypatch):
    def boom(probe):
        raise OSError(2, "No such file", "/home/Jane Doe/private")

    monkeypatch.setattr(hf, "COLLECTORS", {**hf.COLLECTORS, "tools": boom})
    probe, _ = _probe()
    error = hf.collect(probe)["collectors"]["tools"]["error"]
    assert "Jane" not in error and "Doe" not in error and "<path>" in error


# ------------------------------------------------------------------ the identity of a host


def test_the_fingerprint_id_is_stable_and_ignores_the_network_but_not_the_cpu():
    facts = {"cpus": 4, "cpu_vendor": "GenuineIntel", "cpu_model": "Xeon", "cpu_flags": ["avx2"],
             "collectors": {"os": {"release": "6.8"}, "network": {"interfaces": 2}, "virt": {"container": False}}}
    moved = {**facts, "collectors": {**facts["collectors"], "network": {"interfaces": 9, "proxy_env": True}}}
    assert fingerprint_id(facts) == fingerprint_id(moved)
    assert fingerprint_id(facts) != fingerprint_id({**facts, "cpu_model": "Epyc"})
    assert len(fingerprint_id(facts)) == 12


@pytest.mark.parametrize("name", ["os", "cpu", "memory", "gpu", "python", "tools", "virt"])
def test_the_fingerprint_id_changes_with_every_identity_collector(name):
    base = {n: {"k": "a"} for n in ["os", "cpu", "memory", "gpu", "python", "tools", "virt"]}
    facts = {"cpus": 4, "collectors": base}
    changed = {"cpus": 4, "collectors": {**base, name: {"k": "b"}}}
    assert fingerprint_id(facts) != fingerprint_id(changed)


def test_match_report_for_a_host_with_no_task_requirement_is_met_without_fields():
    task = Task("t", {"id": "t"}, Path("."))
    assert match_report(task, {"cpus": 2})["met"] is True
    assert match_report(task, {"cpus": 2})["fields"] == []
