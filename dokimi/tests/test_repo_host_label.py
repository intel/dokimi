# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""`repo run --label-host`: run a task the host misses, and record which requirements it met (contract §4.3).

Four host scenarios end to end: an AVX-512 task on an AMD host, a Windows host, a container, and a GPU host.
The host's fingerprint comes from a fake :class:`Probe`; the grading containers are a :class:`FakeRunner`.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dokimi.cli import build_parser
from dokimi.repos.host import host_facts, log_labelled, match_report, match_summary, probe_host as real_probe_host
from dokimi.repos.host_fingerprint import Probe
from dokimi.process import FakeRunner as HostFakeRunner
from dokimi.process import reply
from dokimi.repos.report import format_report, summarize
from dokimi.repos.workspace import Task
from repo_eval_fixture import make_repo
from test_repo_host import AMD_CPUINFO, INTEL_CPUINFO, RACE, HostRunner, _probe_out, _run

AVX = dict(RACE, host={"cpu_flags": ["avx512f"]})  # no CPU count, no vendor: a flag is the only requirement


def _task(host) -> Task:
    return Task("t", {"id": "t", "host": host}, Path("."))


def _repo(tmp_path):
    root, _ = make_repo(tmp_path, {"fix_avx": AVX, "fix_race": RACE})
    return root


def _host(system="Linux", files=None, calls=None, **kw) -> Probe:
    """A fake host: ``files`` are what its /proc, /sys and /etc read; ``calls`` answer its spawns."""
    files = files or {}
    answers = calls or {}

    def handler(call):
        text = answers.get(" ".join(call.argv))
        return reply(text) if text is not None else reply(returncode=1)

    kw.setdefault("which", lambda name: None)
    return Probe(system=system, read=lambda path: files.get(path), run=HostFakeRunner(handler), env={}, **kw)


def _labelled(monkeypatch, host: Probe) -> None:
    """Probe this batch's host with ``host`` (the container probe is still the fake runner's)."""
    monkeypatch.setattr("dokimi.repos.runner.probe_host", lambda ctx, work: real_probe_host(ctx, work, probe=host))


def _cell_json(out: Path, task: str, name: str) -> dict:
    """The first rep of ``task``'s cell file ``name`` (``result.json`` or ``meta.json``), as an object."""
    return json.loads(next((out / "b1").glob(f"{task}__*__r1/{name}")).read_text(encoding="utf-8"))


# ------------------------------------------------------------------ the match report


def test_an_avx512_task_on_an_amd_host_is_a_labelled_miss_on_the_flag_alone():
    amd = host_facts(_probe_out(affinity=8, cpuinfo=AMD_CPUINFO))
    report = match_report(_task({"cpu_flags": ["avx512f"]}), amd)
    assert report["met"] is False
    assert report["fields"] == [{"field": "cpu_flags.avx512f", "required": "avx512f", "actual": False, "met": False}]
    assert match_summary(report) == "cpu_flags.avx512f missing"


def test_a_met_requirement_and_a_probe_that_could_not_tell_are_recorded_as_such():
    intel = host_facts(_probe_out(affinity=8, cpuinfo=INTEL_CPUINFO))
    met = match_report(_task({"min_cpus": 4, "cpu_vendors": ["GenuineIntel"], "cpu_flags": ["avx2"]}), intel)
    assert met["met"] is True and [f["met"] for f in met["fields"]] == [True, True, True]
    undecided = match_report(_task({"min_cpus": 4, "cpu_flags": ["avx2"]}), {"error": "host probe exited 127"})
    assert undecided["met"] is None and [f["met"] for f in undecided["fields"]] == [None, None]
    assert match_summary(undecided) == "min_cpus unknown, cpu_flags.avx2 unknown"


def test_a_vendor_miss_the_min_cpus_boundary_and_an_uppercase_flag_are_judged_exactly():
    intel = host_facts(_probe_out(affinity=4, cpuinfo=INTEL_CPUINFO))
    vendor = match_report(_task({"cpu_vendors": ["AuthenticAMD"]}), intel)
    assert vendor["met"] is False and match_summary(vendor) == "cpu_vendors missing"
    assert match_report(_task({"cpu_vendors": ["genuineintel"]}), intel)["met"] is True  # vendor compare ignores case
    assert match_report(_task({"min_cpus": 4}), intel)["met"] is True  # exactly min_cpus is enough
    assert match_report(_task({"min_cpus": 5}), intel)["met"] is False
    assert match_report(_task({"cpu_flags": ["AVX2"]}), intel)["met"] is True  # docs write AVX2, cpuinfo avx2


def test_one_unmet_and_one_undecided_field_is_a_miss():
    facts = {"cpus": 2, "cpu_vendor": None, "cpu_flags": []}  # the vendor and flags are unknown, the CPU count is not
    report = match_report(_task({"min_cpus": 4, "cpu_flags": ["avx2"]}), facts)
    assert [f["met"] for f in report["fields"]] == [False, None]
    assert report["met"] is False


def test_a_host_the_probe_could_not_judge_is_logged_as_labelled_and_a_met_host_is_not():
    intel = host_facts(_probe_out(affinity=8, cpuinfo=INTEL_CPUINFO))
    met = match_report(_task({"min_cpus": 4}), intel)
    undecided = match_report(_task({"min_cpus": 4}), {"error": "host probe exited 127"})
    missed = match_report(_task({"min_cpus": 16}), intel)
    lines: list[str] = []
    log_labelled({"met": met, "undecided": undecided, "missed": missed}, lines.append)
    assert lines == [
        f"undecided: host {undecided['fingerprint']} is labelled, not blocked: min_cpus unknown",
        f"missed: host {missed['fingerprint']} is labelled, not blocked: min_cpus missing",
    ]


# ------------------------------------------------------------------ the four host scenarios


def test_label_runs_the_avx512_task_on_an_amd_host_and_records_the_miss(tmp_path, monkeypatch, capsys):
    root = _repo(tmp_path)
    out = tmp_path / "runs"
    _labelled(monkeypatch, _host(files={"/proc/cpuinfo": AMD_CPUINFO}))
    _run(root, out, HostRunner(_probe_out(affinity=8, cpuinfo=AMD_CPUINFO)), monkeypatch, "--label-host",
         "--task", "fix_avx")
    cell = _cell_json(out, "fix_avx", "result.json")
    assert cell["host_match"]["met"] is False
    assert cell["host_match"]["fingerprint"] == cell["host_fingerprint"]
    assert [f["field"] for f in cell["host_match"]["fields"] if f["met"] is False] == ["cpu_flags.avx512f"]
    assert not list((out / "b1").glob("fix_avx__*/unsupported.json"))  # ran, not marked unsupported
    logged = capsys.readouterr()
    assert "labelled, not blocked" in logged.out + logged.err


def test_without_label_the_same_task_on_the_same_host_is_unsupported(tmp_path, monkeypatch):
    """Regression: block stays the default; the labelled cells exist only with --label-host."""
    root = _repo(tmp_path)
    out = tmp_path / "runs"
    _labelled(monkeypatch, _host(files={"/proc/cpuinfo": AMD_CPUINFO}))
    with pytest.raises(SystemExit) as exc:  # every cell unsupported: `repo run` has nothing and exits 1
        _run(root, out, HostRunner(_probe_out(affinity=8, cpuinfo=AMD_CPUINFO)), monkeypatch, "--task", "fix_avx")
    assert exc.value.code == 1
    assert len(list((out / "b1").glob("fix_avx__*/unsupported.json"))) == 2
    assert not list((out / "b1").glob("fix_avx__*/result.json"))


def test_label_on_a_windows_host_groups_under_the_windows_fingerprint(tmp_path, monkeypatch):
    """The requirements are checked against the grading container's CPU view, so a Linux container on a
    Windows Docker host meets them; the host's own collectors are what the fingerprint records."""
    root = _repo(tmp_path)
    out = tmp_path / "runs"
    _labelled(monkeypatch, _host(system="Windows", windows_memory=lambda: 8 * 2**30,
                                 calls={"git --version": "git version 2.45.1\n"},
                                 which=lambda name: r"C:\git.exe" if name == "git" else None))
    _run(root, out, HostRunner(_probe_out(affinity=8)), monkeypatch, "--label-host", "--task", "fix_avx")
    cell = _cell_json(out, "fix_avx", "result.json")
    assert cell["host_match"]["met"] is True and cell["host_match"]["fields"][0]["field"] == "cpu_flags.avx512f"
    meta = _cell_json(out, "fix_avx", "meta.json")
    assert meta["host"]["collectors"]["os"]["system"] == "Windows"
    assert meta["host"]["collectors"]["memory"] == {"total_bytes": 8 * 2**30}
    assert meta["host"]["collectors"]["tools"]["git"] == "2.45.1"


def test_label_on_a_container_records_the_container_in_the_fingerprint(tmp_path, monkeypatch):
    root = _repo(tmp_path)
    out = tmp_path / "runs"
    _labelled(monkeypatch, _host(files={"/.dockerenv": "", "/proc/cpuinfo": INTEL_CPUINFO}))
    _run(root, out, HostRunner(_probe_out(affinity=8)), monkeypatch, "--label-host", "--task", "fix_race")
    meta = _cell_json(out, "fix_race", "meta.json")
    assert meta["host"]["collectors"]["virt"]["container"] is True
    assert meta["host"]["collectors"]["virt"]["container_kind"] == "docker"
    assert meta["host_match"]["met"] is True  # RACE needs 4 CPUs of GenuineIntel, the container's cgroup gives 8


def test_label_on_a_gpu_host_records_the_driver_and_cuda_version(tmp_path, monkeypatch):
    root = _repo(tmp_path)
    out = tmp_path / "runs"
    calls = {"nvidia-smi --query-gpu=name,driver_version --format=csv,noheader": "A100, 550.54.14\n",
             "nvidia-smi": "CUDA Version: 12.4"}
    _labelled(monkeypatch, _host(calls=calls, which=lambda name: "/usr/bin/nvidia-smi" if name == "nvidia-smi" else None))
    _run(root, out, HostRunner(_probe_out(affinity=8, cpuinfo=AMD_CPUINFO)), monkeypatch, "--label-host",
         "--task", "fix_avx")
    meta = _cell_json(out, "fix_avx", "meta.json")
    assert meta["host"]["collectors"]["gpu"]["cuda_version"] == "12.4"
    assert meta["host"]["collectors"]["gpu"]["driver_version"] == "550.54.14"


# ------------------------------------------------------------------ results group by the actual fingerprint


def test_report_splits_a_batch_by_the_fingerprint_of_the_host_that_ran_each_cell(tmp_path, monkeypatch):
    root = _repo(tmp_path)
    out = tmp_path / "runs"
    _labelled(monkeypatch, _host(files={"/proc/cpuinfo": AMD_CPUINFO}))
    _run(root, out, HostRunner(_probe_out(affinity=8, cpuinfo=AMD_CPUINFO)), monkeypatch, "--label-host",
         "--task", "fix_avx")
    first = sorted((out / "b1").glob("fix_avx__*/result.json"))[1]
    first.unlink()  # the second rep is run again, on another host
    _labelled(monkeypatch, _host(files={"/proc/cpuinfo": INTEL_CPUINFO}))
    _run(root, out, HostRunner(_probe_out(affinity=8, cpuinfo=INTEL_CPUINFO)), monkeypatch, "--label-host",
         "--task", "fix_avx")
    rows = [r for r in summarize(out / "b1")["rows"] if r["task"] == "fix_avx"]
    assert len(rows) == 2 and rows[0]["host_fingerprint"] != rows[1]["host_fingerprint"]
    assert [r["n"] for r in rows] == [1, 1]
    assert sorted(r["host_match"]["met"] for r in rows) == [False, True]  # AMD misses avx512f, Intel has it
    assert "labelled, unmet: cpu_flags.avx512f" in format_report(summarize(out / "b1"))


def test_the_flag_is_off_by_default_and_on_with_label_host():
    argv = ["repo", "run", "/x@HEAD", "--arm", "guidance:raw", "--model", "m", "--batch", "b"]
    assert build_parser().parse_args(argv).label_host is False
    assert build_parser().parse_args([*argv, "--label-host"]).label_host is True


def test_compare_warns_when_a_labelled_cell_ran_on_a_host_that_misses_its_task(tmp_path):
    from dokimi.repos.compare import _arm_warnings

    def cell(arm, met):
        rec = {"task": "race", "arm": arm, "model": "m-1", "batch": "b"}
        return rec if met is None else {**rec, "host_match": {"met": met, "fields": [], "fingerprint": "f"}}

    cells = [cell("raw", None), cell("skill", False)]  # the treatment ran one labelled cell on a host that misses
    assert any("race" in w and "--label-host" in w for w in _arm_warnings(cells, "raw", "skill", None))
    undecided = [cell("raw", None), {**cell("skill", True), "host_match": {"met": None, "fields": [], "fingerprint": "f"}}]
    assert any("race" in w and "--label-host" in w for w in _arm_warnings(undecided, "raw", "skill", None))
    assert not any("--label-host" in w for w in _arm_warnings([cell("raw", None), cell("skill", None)], "raw", "skill", None))


def test_a_probe_container_that_fails_once_is_retried_and_its_retry_is_kept(tmp_path):
    from dokimi.repos.container import ContainerResult
    from dokimi.repos.grading import EvalContext
    from dokimi.repos.manifest import parse_manifest
    from repo_eval_fixture import MANIFEST

    class FailsOnce(HostRunner):
        def run(self, spec, *, stdout, stderr, timeout_s):
            if not self.probes:
                self.probes.append(spec)
                stderr.write_text("timed out\n")
                return ContainerResult(returncode=1, timed_out=True, wall_s=0.0)
            return super().run(spec, stdout=stdout, stderr=stderr, timeout_s=timeout_s)

    runner = FailsOnce({"affinity": 4, "cpuinfo": "vendor_id : GenuineIntel\nflags : avx2"})
    ctx = EvalContext(manifest=parse_manifest(MANIFEST), git_dir=tmp_path, content_dir=tmp_path, image="img:1",
                      runner=runner)
    facts = real_probe_host(ctx, tmp_path / "w")
    assert "error" not in facts and facts["cpus"] == 4 and len(runner.probes) == 2
