# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""task.json `host` requirements: probe, `repo tasks check`, `repo run`, `repo report` (contract §3, §4.3).

No docker, no LLM: the probe container is answered by a fake runner, or run on this host.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from dokimi.cli import build_parser
from dokimi.repos.container import ContainerResult, ContainerSpec
from dokimi.repos.grading import EvalContext
from dokimi.repos.host import (
    PROBE,
    HostRequirements,
    HostSpecError,
    cgroup_cpus,
    host_facts,
    probe_host,
    requirements,
    unmet,
)
from dokimi.repos.manifest import parse_manifest
from dokimi.repos.report import format_report, load_cells, load_unsupported, summarize
from dokimi.repos.runner import UNSUPPORTED_FILE, _mark_unsupported, run_batch
from dokimi.repos.workspace import Task
from repo_eval_fixture import MANIFEST, FakeRunner, fake_harness, make_repo

INTEL_CPUINFO = ("processor\t: 0\nvendor_id\t: GenuineIntel\nmodel name\t: Intel(R) Xeon(R) Platinum 8480+\n"
                 "flags\t\t: fpu sse2 avx2 avx512f\n")
AMD_CPUINFO = "processor\t: 0\nvendor_id\t: AuthenticAMD\nmodel name\t: AMD EPYC 7763\nflags\t\t: fpu sse2 avx2\n"
RACE = {"family": "fix", "grader": "calc", "gate": ["tests_pass"], "setup_patch": "setup.patch",
        "prompt": "add() races; fix it.", "host": {"min_cpus": 4, "cpu_vendors": ["GenuineIntel"]}}


def _probe_out(affinity=8, cpuinfo=INTEL_CPUINFO, cpu_max="max 100000\n"):
    return {"affinity": affinity, "cpu_max": cpu_max, "cfs_quota_us": None, "cfs_period_us": None,
            "cpuinfo": cpuinfo}


class HostRunner(FakeRunner):
    """:class:`FakeRunner` whose host-probe container prints ``probe`` (or fails when it is ``None``)."""

    def __init__(self, probe):
        super().__init__()
        self.probe = probe
        self.probes: list[ContainerSpec] = []

    def run(self, spec, *, stdout, stderr, timeout_s):
        if spec.argv[:3] == ["python3", "-c", PROBE]:
            self.probes.append(spec)
            stdout.write_text("" if self.probe is None else json.dumps(self.probe) + "\n")
            stderr.write_text("python3: not found\n" if self.probe is None else "")
            return ContainerResult(returncode=0 if self.probe is not None else 127, timed_out=False, wall_s=0.0)
        return super().run(spec, stdout=stdout, stderr=stderr, timeout_s=timeout_s)


def _task(host) -> Task:
    return Task("t", {"id": "t", "host": host}, Path("."))


# ------------------------------------------------------------------ the probe's facts


@pytest.mark.parametrize("raw, cpus", [
    ({"cpu_max": "max 100000\n"}, None),
    ({"cpu_max": "200000 100000\n"}, 2),
    ({"cpu_max": "150000 100000\n"}, 2),          # docker --cpus 1.5: a runtime rounds up
    ({"cpu_max": "50000 100000"}, 1),
    ({"cfs_quota_us": "-1\n", "cfs_period_us": "100000\n"}, None),   # cgroup v1, unlimited
    ({"cfs_quota_us": "300000\n", "cfs_period_us": "100000\n"}, 3),  # cgroup v1, --cpus 3
    ({"cpu_max": "garbage"}, None),
    ({}, None),
])
def test_cgroup_quota_in_cpus(raw, cpus):
    assert cgroup_cpus(raw) == cpus


def test_cpus_are_the_affinity_capped_by_the_quota_and_cpuinfo_gives_vendor_and_flags():
    facts = host_facts(_probe_out(affinity=16, cpu_max="200000 100000\n"))
    assert facts == {"cpus": 2, "affinity_cpus": 16, "cgroup_cpus": 2, "cpu_vendor": "GenuineIntel",
                     "cpu_model": "Intel(R) Xeon(R) Platinum 8480+",
                     "cpu_flags": ["avx2", "avx512f", "fpu", "sse2"]}
    assert host_facts(_probe_out(affinity=2, cpu_max="800000 100000"))["cpus"] == 2  # --cpuset-cpus 0-1
    assert host_facts(_probe_out(affinity=12))["cpus"] == 12
    arm = host_facts(_probe_out(cpuinfo="processor\t: 0\nFeatures\t: fp asimd sve\nCPU implementer\t: 0x41\n"))
    assert arm["cpu_vendor"] is None and arm["cpu_flags"] == ["asimd", "fp", "sve"]


def test_the_probe_runs_in_the_image_like_the_grading_container(tmp_path):
    """FakeRunner runs the probe with this interpreter: its count is this process's own view."""
    runner = FakeRunner()
    ctx = EvalContext(manifest=parse_manifest(MANIFEST), git_dir=tmp_path, content_dir=tmp_path, image="img:1",
                      runner=runner)
    facts = probe_host(ctx, tmp_path / "w")
    (spec,) = runner.specs
    assert spec.image == "img:1" and spec.network == "none" and not spec.mounts and spec.argv[:2] == ["python3", "-c"]
    affinity = len(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else os.cpu_count()
    assert facts["affinity_cpus"] == affinity and 1 <= facts["cpus"] <= affinity
    assert "error" not in facts


def test_a_probe_that_cannot_run_is_recorded_and_leaves_requirements_undecided(tmp_path):
    ctx = EvalContext(manifest=parse_manifest(MANIFEST), git_dir=tmp_path, content_dir=tmp_path, image="img:1",
                      runner=HostRunner(None))
    facts = probe_host(ctx, tmp_path / "w")
    assert facts["error"].startswith("host probe exited 127 in img:1") and "python3: not found" in facts["error"]
    reason, unsupported_by = unmet(_task(None), facts)
    assert reason is None  # a task without requirements does not care
    reason, unsupported_by = unmet(_task({"min_cpus": 2}), facts)
    assert reason is not None and "cannot tell on this host: host probe exited 127" in reason
    assert unsupported_by == "host-probe"  # marked as probe failure, not hardware miss


# ------------------------------------------------------------------ requirements


@pytest.mark.parametrize("host, problem", [
    ([4], "must be an object"),
    ({"min_cpu": 4}, "unknown key(s) min_cpu"),
    ({"min_cpus": "4"}, "min_cpus must be an integer >= 1, got '4'"),
    ({"min_cpus": True}, "min_cpus must be an integer >= 1"),
    ({"min_cpus": 0}, "min_cpus must be an integer >= 1"),
    ({"min_cpus": 2.5}, "min_cpus must be an integer >= 1"),
    ({"cpu_vendors": "GenuineIntel"}, "cpu_vendors must be a list of non-empty strings"),
    ({"cpu_flags": ["avx2", 3]}, "cpu_flags must be a list of non-empty strings"),
    ({"cpu_flags": [""]}, "cpu_flags must be a list of non-empty strings"),
])
def test_a_bad_host_shape_is_a_clear_error(host, problem):
    with pytest.raises(HostSpecError) as exc:
        requirements(_task(host))
    assert str(exc.value).startswith("task t: task.json host") and problem in str(exc.value)


def test_requirements_default_and_parse():
    assert requirements(_task(None)) is None
    assert requirements(_task({})) is None  # an empty object asks for nothing
    assert requirements(_task({"min_cpus": 1})) is None
    assert requirements(_task({"min_cpus": 3, "cpu_vendors": ["GenuineIntel"], "cpu_flags": ["avx512f"]})) == \
        HostRequirements(3, ("GenuineIntel",), ("avx512f",))


def test_unmet_names_every_shortfall_and_what_the_container_saw():
    amd = host_facts(_probe_out(affinity=2, cpuinfo=AMD_CPUINFO))
    why, unsupported_by = unmet(_task({"min_cpus": 4, "cpu_vendors": ["GenuineIntel"], "cpu_flags": ["avx512f", "avx2"]}), amd)
    assert why is not None and unsupported_by == "host"
    assert "2 CPU(s) < min_cpus 4" in why and "vendor AuthenticAMD not in GenuineIntel" in why
    assert "missing cpu_flags avx512f" in why and "avx2," not in why.split("has")[1]
    assert "AMD EPYC 7763" in why and "affinity 2, cgroup quota none" in why
    intel = host_facts(_probe_out(affinity=8))
    reason, _ = unmet(_task({"min_cpus": 4, "cpu_vendors": ["GenuineIntel"], "cpu_flags": ["avx512f"]}), intel)
    assert reason is None
    reason, _ = unmet(_task({}), amd)
    assert reason is None


# ------------------------------------------------------------------ tasks check, run, report


def _repo(tmp_path, host=None):
    task = dict(RACE, host=host) if host is not None else RACE
    root, _ = make_repo(tmp_path, {"fix_race": task})
    return root


def _cli(argv, monkeypatch, runner):
    monkeypatch.setattr("dokimi.repos.container.DockerRunner", lambda *a, **k: runner)
    args = build_parser().parse_args(argv)
    args.func(args)


def _check(root, out, runner, monkeypatch, *extra):
    _cli(["repo", "tasks", "check", f"{root}@HEAD", "--manifest", ".agent-eval/repo-eval.yaml", "--out", str(out),
          "--cache-root", str(out / "caches"), *extra], monkeypatch, runner)


def test_tasks_check_reports_a_task_the_host_cannot_show_as_unsupported(tmp_path, monkeypatch, capsys):
    root = _repo(tmp_path)
    runner = HostRunner(_probe_out(affinity=2, cpuinfo=AMD_CPUINFO))
    with pytest.raises(SystemExit) as exc:
        _check(root, tmp_path / "a", runner, monkeypatch)
    assert exc.value.code == 1  # as for an egress task: not ok
    doc = json.loads((tmp_path / "a/check/check.json").read_text())
    verdicts = {t["task"]: t for t in doc["tasks"]}
    race = verdicts["fix_race"]
    assert race["verdict"] == "unsupported" and race["problems"][0].startswith("host: needs min_cpus 4")
    assert "2 CPU(s) < min_cpus 4" in race["problems"][0] and "null" not in race and "oracle" not in race
    assert verdicts["fix_add"]["verdict"] == "ok" and verdicts["review_add"]["verdict"] == "ok"
    assert doc["host"]["cpus"] == 2 and doc["host"]["cpu_vendor"] == "AuthenticAMD"
    assert len(runner.probes) == 1  # once per check, not per task
    out = capsys.readouterr().out
    assert "fix_race: UNSUPPORTED\n  - host: needs" in out and "2/3 ok" in out

    _check(root, tmp_path / "b", HostRunner(_probe_out(affinity=8)), monkeypatch, "--task", "fix_race")
    doc = json.loads((tmp_path / "b/check/check.json").read_text())
    assert doc["tasks"][0]["verdict"] == "ok" and doc["host"]["cpus"] == 8


def test_tasks_check_refuses_a_bad_host_shape_before_any_container(tmp_path, monkeypatch, capsys):
    root = _repo(tmp_path, host={"min_cpus": "4"})
    runner = HostRunner(_probe_out())
    with pytest.raises(SystemExit) as exc:
        _check(root, tmp_path / "a", runner, monkeypatch)
    assert exc.value.code == 1 and not runner.specs
    assert "task fix_race: task.json host.min_cpus must be an integer >= 1, got '4'" in capsys.readouterr().err


def test_allow_unsupported_lets_host_unsupported_tasks_pass_the_exit_code(tmp_path, monkeypatch, capsys):
    root = _repo(tmp_path)
    amd = _probe_out(affinity=2, cpuinfo=AMD_CPUINFO)
    _check(root, tmp_path / "a", HostRunner(amd), monkeypatch, "--allow-unsupported")  # exits 0
    out = capsys.readouterr().out
    assert "fix_race: UNSUPPORTED\n  - host: needs min_cpus 4" in out and "2/3 ok" in out
    assert "1 unsupported on this host, allowed by --allow-unsupported: fix_race" in out
    doc = json.loads((tmp_path / "a/check/check.json").read_text())
    race = next(t for t in doc["tasks"] if t["task"] == "fix_race")
    assert race["verdict"] == "unsupported" and race["unsupported_by"] == "host"


@pytest.mark.parametrize("extra, why", [
    ({"fix_nobug": {"family": "fix", "grader": "calc", "gate": ["tests_pass"], "prompt": "x"}}, "broken"),
    ({"fix_pip": {"family": "fix", "grader": "calc", "egress": ["pypi.org"], "prompt": "x"}}, "egress"),
])
def test_allow_unsupported_still_fails_broken_and_egress_tasks(tmp_path, monkeypatch, capsys, extra, why):
    root, _ = make_repo(tmp_path, {"fix_race": RACE, **extra})
    (task,) = extra
    with pytest.raises(SystemExit) as exc:
        _check(root, tmp_path / "a", HostRunner(_probe_out(affinity=2, cpuinfo=AMD_CPUINFO)), monkeypatch,
               "--allow-unsupported", "--task", "fix_race", "--task", task)
    assert exc.value.code == 1
    res = {t["task"]: t for t in json.loads((tmp_path / "a/check/check.json").read_text())["tasks"]}
    assert res["fix_race"]["unsupported_by"] == "host"
    if why == "broken":
        assert res[task]["verdict"] == "broken"
    else:
        assert res[task]["verdict"] == "unsupported" and res[task]["unsupported_by"] == "egress"


def _run(root, out, runner, monkeypatch, *extra):
    exe, profile = fake_harness(out.parent)
    monkeypatch.setenv("FAKE_ACTION", "fix")
    _cli(["repo", "run", f"{root}@HEAD", "--manifest", ".agent-eval/repo-eval.yaml", "--harness", str(profile),
          "--bin", str(exe), "--model", "m-1", "--batch", "b1", "--out", str(out), "--cache-root",
          str(out / "caches"), "--agent-network", "host", "--arm", "guidance:raw", "--reps", "2", *extra],
         monkeypatch, runner)


def test_run_skips_unsupported_cells_and_the_report_shows_them(tmp_path, monkeypatch, capsys):
    root = _repo(tmp_path)
    out = tmp_path / "runs"
    runner = HostRunner(_probe_out(affinity=2, cpuinfo=AMD_CPUINFO))
    _run(root, out, runner, monkeypatch, "--task", "fix_add", "--task", "fix_race")  # exits 0: nothing crashed
    batch = out / "b1"
    assert len(runner.probes) == 1  # once per run, not per cell
    race = batch / "fix_race__guidance-raw__m-1__r1"
    assert sorted(p.name for p in race.iterdir()) == ["unsupported.json"]
    marker = json.loads((race / "unsupported.json").read_text())
    assert marker["task"] == "fix_race" and marker["rep"] == 1 and "2 CPU(s) < min_cpus 4" in marker["reason"]
    assert marker["host"]["cpu_vendor"] == "AuthenticAMD"
    agents = [s for s in runner.specs if "-p" in s.argv]
    assert len(agents) == 2 and all("fix_race" not in str(m.host) for s in agents for m in s.mounts)
    meta = json.loads((batch / "fix_add__guidance-raw__m-1__r1/meta.json").read_text())
    assert meta["host"]["cpus"] == 2 and meta["host"]["cpu_model"] == "AMD EPYC 7763"
    assert json.loads((batch / "_eval/host.json").read_text())["cpus"] == 2
    logged = capsys.readouterr()
    assert "fix_race: UNSUPPORTED, its cells are not run: needs min_cpus 4" in logged.out + logged.err
    assert "2 ran, 0 already done, 0 crashed" in logged.out

    summary = summarize(batch)
    assert summary["missing"] == [] and summary["cells"] == 2
    assert [r["task"] for r in summary["rows"]] == ["fix_add"]
    assert [(u["task"], u["cell"]) for u in summary["unsupported"]] == [
        ("fix_race", "fix_race__guidance-raw__m-1__r1"), ("fix_race", "fix_race__guidance-raw__m-1__r2")]
    text = format_report(summary)
    assert "unsupported (not run, not failed): fix_race, 2 cell(s): needs min_cpus 4" in text
    assert "0 missing" in text

    # On a host that meets the requirements the same batch runs them, and the markers go.
    capable = HostRunner(_probe_out(affinity=8))
    _run(root, out, capable, monkeypatch, "--task", "fix_add", "--task", "fix_race")
    assert "2 ran, 2 already done" in capsys.readouterr().out
    assert not list(batch.glob("*/unsupported.json"))
    summary = summarize(batch)
    assert summary["unsupported"] == [] and summary["cells"] == 4
    assert {r["task"]: r["successes"] for r in summary["rows"]} == {"fix_add": 2, "fix_race": 2}

    # Back on the small host, a finished cell keeps its result, even with --force.
    small = HostRunner(_probe_out(affinity=2, cpuinfo=AMD_CPUINFO))
    _run(root, out, small, monkeypatch, "--task", "fix_race", "--force")
    assert "0 ran, 2 already done" in capsys.readouterr().out
    assert len(load_cells(batch)[0]) == 4 and not list(batch.glob("*/unsupported.json"))


def test_a_capable_run_that_stops_early_leaves_the_unreached_cells_missing(tmp_path, monkeypatch, capsys):
    root = _repo(tmp_path)
    out = tmp_path / "runs"
    _run(root, out, HostRunner(_probe_out(affinity=2, cpuinfo=AMD_CPUINFO)), monkeypatch, "--task", "fix_race")
    with pytest.raises(SystemExit):  # the budget stops it after one cell
        _run(root, out, HostRunner(_probe_out(affinity=8)), monkeypatch, "--task", "fix_race",
             "--max-batch-usd", "0.005")
    assert "1 ran, 0 already done, 0 crashed, 1 not started" in capsys.readouterr().out
    summary = summarize(out / "b1")
    assert summary["unsupported"] == [] and summary["missing"] == ["fix_race__guidance-raw__m-1__r2"]


def test_run_refuses_a_bad_host_shape_before_any_agent(tmp_path, monkeypatch, capsys):
    root = _repo(tmp_path, host={"cpu_flags": "avx512f"})
    runner = HostRunner(_probe_out())
    with pytest.raises(SystemExit) as exc:
        _run(root, tmp_path / "runs", runner, monkeypatch)
    assert exc.value.code == 1 and not [s for s in runner.specs if "-p" in s.argv]
    assert "task fix_race: task.json host.cpu_flags must be a list of non-empty strings" in capsys.readouterr().err
    # Regression: validate host requirements immediately after load_tasks, before harness_version/isolation_probe.
    # The test confirms zero containers start before the host requirement error, not just zero agent containers.
    assert len(runner.specs) == 0


def _run_probe(root: Path, cgroup: str, mountinfo: str, tree: dict[str, str]) -> dict:
    """Run PROBE for real against a fake /proc and /sys under ``root``."""
    files = {"proc/self/cgroup": cgroup, "proc/self/mountinfo": mountinfo,
             "proc/cpuinfo": INTEL_CPUINFO, **tree}
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")
    out = subprocess.run([sys.executable, "-c", PROBE], capture_output=True, text=True, check=True,
                         env={**os.environ, "DOKIMI_PROBE_ROOT": str(root)})
    return json.loads(out.stdout.strip().splitlines()[-1])


V2_MOUNT = "35 25 0:30 / /sys/fs/cgroup rw,nosuid - cgroup2 cgroup2 rw\n"


@pytest.mark.parametrize("cgroup, tree", [
    # private cgroup namespace: the container's cgroup is the mount root
    ("0::/\n", {"sys/fs/cgroup/cpu.max": "200000 100000\n"}),
    # host namespace: unlimited root, the container's own limit one level down
    ("0::/docker/eval\n", {"sys/fs/cgroup/cpu.max": "max 100000\n",
                            "sys/fs/cgroup/docker/eval/cpu.max": "200000 100000\n"}),
    # a visible ancestor limits, the container's own cgroup is unlimited
    ("0::/docker/eval\n", {"sys/fs/cgroup/cpu.max": "max 100000\n",
                            "sys/fs/cgroup/docker/cpu.max": "200000 100000\n",
                            "sys/fs/cgroup/docker/eval/cpu.max": "max 100000\n"}),
    # two limits: the tighter ancestor wins over the container's looser one
    ("0::/docker/eval\n", {"sys/fs/cgroup/cpu.max": "max 100000\n",
                            "sys/fs/cgroup/docker/cpu.max": "200000 100000\n",
                            "sys/fs/cgroup/docker/eval/cpu.max": "400000 100000\n"}),
])
def test_probe_resolves_the_containers_cgroup_v2_limit(tmp_path, cgroup, tree):
    raw = _run_probe(tmp_path, cgroup, V2_MOUNT, tree)
    assert host_facts(raw)["cgroup_cpus"] == 2


def test_probe_walks_from_the_mount_root_when_the_mount_shows_only_a_subtree(tmp_path):
    # mountinfo field 4 is /kubepods: the container's /kubepods/pod1 is the mount's /pod1
    mount = "35 25 0:30 /kubepods /sys/fs/cgroup rw,nosuid - cgroup2 cgroup2 rw\n"
    raw = _run_probe(tmp_path, "0::/kubepods/pod1\n", mount,
                     {"sys/fs/cgroup/pod1/cpu.max": "200000 100000\n", "sys/fs/cgroup/cpu.max": "max 100000\n"})
    assert host_facts(raw)["cgroup_cpus"] == 2


def test_probe_walks_from_the_v1_mount_root_when_the_mount_shows_only_a_subtree(tmp_path):
    mount = "31 25 0:27 /kubepods /sys/fs/cgroup/cpu,cpuacct rw,nosuid,nodev - cgroup cgroup rw,cpu,cpuacct\n"
    raw = _run_probe(tmp_path, "4:cpu,cpuacct:/kubepods/pod1\n", mount,
                     {"sys/fs/cgroup/cpu,cpuacct/pod1/cpu.cfs_quota_us": "200000\n",
                      "sys/fs/cgroup/cpu,cpuacct/pod1/cpu.cfs_period_us": "100000\n",
                      "sys/fs/cgroup/cpu,cpuacct/cpu.cfs_quota_us": "-1\n",
                      "sys/fs/cgroup/cpu,cpuacct/cpu.cfs_period_us": "100000\n"})
    assert host_facts(raw)["cgroup_cpus"] == 2


# The mount root /kube is a string prefix of /kubepods but not a path prefix: the walk must not strip it.
def test_probe_does_not_strip_a_mount_root_that_is_only_a_string_prefix_of_the_cgroup(tmp_path):
    mount = "35 25 0:30 /kube /sys/fs/cgroup rw,nosuid - cgroup2 cgroup2 rw\n"
    raw = _run_probe(tmp_path, "0::/kubepods/p\n", mount,
                     {"sys/fs/cgroup/kubepods/p/cpu.max": "200000 100000\n", "sys/fs/cgroup/cpu.max": "max 100000\n"})
    assert host_facts(raw)["cgroup_cpus"] == 2


def test_probe_does_not_strip_a_v1_mount_root_that_is_only_a_string_prefix_of_the_cgroup(tmp_path):
    mount = "31 25 0:27 /kube /sys/fs/cgroup/cpu,cpuacct rw,nosuid,nodev - cgroup cgroup rw,cpu,cpuacct\n"
    raw = _run_probe(tmp_path, "4:cpu,cpuacct:/kubepods/p\n", mount,
                     {"sys/fs/cgroup/cpu,cpuacct/kubepods/p/cpu.cfs_quota_us": "200000\n",
                      "sys/fs/cgroup/cpu,cpuacct/kubepods/p/cpu.cfs_period_us": "100000\n",
                      "sys/fs/cgroup/cpu,cpuacct/cpu.cfs_quota_us": "-1\n",
                      "sys/fs/cgroup/cpu,cpuacct/cpu.cfs_period_us": "100000\n"})
    assert host_facts(raw)["cgroup_cpus"] == 2


def test_probe_reads_the_v1_quota_without_a_cgroup2_mount(tmp_path):
    raw = _run_probe(tmp_path, "4:cpu,cpuacct:/docker/eval\n", "",
                     {"sys/fs/cgroup/cpu/cpu.cfs_quota_us": "300000\n",
                      "sys/fs/cgroup/cpu/cpu.cfs_period_us": "100000\n"})
    assert host_facts(raw)["cgroup_cpus"] == 3


V1_CPU_MOUNT = "31 25 0:27 / /sys/fs/cgroup/cpu,cpuacct rw,nosuid,nodev - cgroup cgroup rw,cpu,cpuacct\n"


@pytest.mark.parametrize("tree, cpus", [
    # combined controller, the container's own quota one level down in the host namespace
    ({"sys/fs/cgroup/cpu,cpuacct/cpu.cfs_quota_us": "-1\n", "sys/fs/cgroup/cpu,cpuacct/cpu.cfs_period_us": "100000\n",
      "sys/fs/cgroup/cpu,cpuacct/docker/eval/cpu.cfs_quota_us": "200000\n",
      "sys/fs/cgroup/cpu,cpuacct/docker/eval/cpu.cfs_period_us": "100000\n"}, 2),
    # a visible ancestor limits, the container's own cgroup is unlimited
    ({"sys/fs/cgroup/cpu,cpuacct/docker/cpu.cfs_quota_us": "150000\n",
      "sys/fs/cgroup/cpu,cpuacct/docker/cpu.cfs_period_us": "100000\n",
      "sys/fs/cgroup/cpu,cpuacct/docker/eval/cpu.cfs_quota_us": "-1\n",
      "sys/fs/cgroup/cpu,cpuacct/docker/eval/cpu.cfs_period_us": "100000\n"}, 2),
    # two limits: the tighter ancestor wins over the container's looser one
    ({"sys/fs/cgroup/cpu,cpuacct/docker/cpu.cfs_quota_us": "200000\n",
      "sys/fs/cgroup/cpu,cpuacct/docker/cpu.cfs_period_us": "100000\n",
      "sys/fs/cgroup/cpu,cpuacct/docker/eval/cpu.cfs_quota_us": "400000\n",
      "sys/fs/cgroup/cpu,cpuacct/docker/eval/cpu.cfs_period_us": "100000\n"}, 2),
])
def test_probe_resolves_a_combined_v1_cpu_controller_and_its_ancestors(tmp_path, tree, cpus):
    raw = _run_probe(tmp_path, "4:cpu,cpuacct:/docker/eval\n", V1_CPU_MOUNT, tree)
    assert host_facts(raw)["cgroup_cpus"] == cpus


V1_CPU_TREE = {"sys/fs/cgroup/cpu,cpuacct/docker/cpu.cfs_quota_us": "200000\n",
               "sys/fs/cgroup/cpu,cpuacct/docker/cpu.cfs_period_us": "100000\n",
               "sys/fs/cgroup/cpu,cpuacct/docker/eval/cpu.cfs_quota_us": "-1\n",
               "sys/fs/cgroup/cpu,cpuacct/docker/eval/cpu.cfs_period_us": "100000\n"}


def test_probe_takes_the_cpu_mount_not_an_earlier_cpuset_mount(tmp_path):
    cpuset = "30 25 0:26 / /sys/fs/cgroup/cpuset rw - cgroup cgroup rw,cpuset\n"
    raw = _run_probe(tmp_path, "3:cpuset:/docker/eval\n4:cpu,cpuacct:/docker/eval\n", cpuset + V1_CPU_MOUNT,
                     {**V1_CPU_TREE, "sys/fs/cgroup/cpuset/docker/eval/cpuset.cpus": "0-1\n"})
    assert host_facts(raw)["cgroup_cpus"] == 2


def test_probe_takes_the_cpu_cgroup_line_not_a_later_cpuset_line(tmp_path):
    raw = _run_probe(tmp_path, "4:cpu,cpuacct:/docker/eval\n3:cpuset:/other\n", V1_CPU_MOUNT, V1_CPU_TREE)
    assert host_facts(raw)["cgroup_cpus"] == 2


def test_probe_reports_the_first_cpuinfo_block_of_a_multi_cpu_host(tmp_path):
    raw = _run_probe(tmp_path, "0::/\n", V2_MOUNT,
                     {"sys/fs/cgroup/cpu.max": "200000 100000\n", "proc/cpuinfo": INTEL_CPUINFO + "\n" + AMD_CPUINFO})
    assert host_facts(raw)["cpu_vendor"] == "GenuineIntel"


def test_a_host_with_exactly_min_cpus_is_supported():
    facts = host_facts(_probe_out(affinity=4))
    assert unmet(_task({"min_cpus": 4}), facts) == (None, None)
    assert unmet(_task({"min_cpus": 5}), facts)[1] == "host"


def test_cpu_flags_match_regardless_of_case():
    facts = host_facts(_probe_out())
    assert unmet(_task({"cpu_flags": ["AVX512F"]}), facts) == (None, None)
    assert unmet(_task({"cpu_flags": ["AVX512_VNNI"]}), facts)[1] == "host"
    # the host's flags may come in any case too; a required flag in upper case still matches them
    upper = {"cpus": 8, "cpu_vendor": "GenuineIntel", "cpu_flags": ["AVX512F"]}
    assert unmet(_task({"cpu_flags": ["avx512f"]}), upper) == (None, None)
    assert unmet(_task({"cpu_flags": ["AVX512F"]}), upper) == (None, None)


def test_probe_failure_is_marked_host_probe_never_hidden_by_allow_unsupported(tmp_path, monkeypatch, capsys):
    """Regression: probe failure (unsupported_by='host-probe') must not be hidden by --allow-unsupported.

    A broken probe is different from a real hardware miss. When python3 is not in the image,
    --allow-unsupported should not hide the need to verify requirements.
    """
    root = _repo(tmp_path)
    runner = HostRunner(None)  # Probe returns None (fails: python3 not found)
    with pytest.raises(SystemExit) as exc:
        _check(root, tmp_path / "a", runner, monkeypatch, "--allow-unsupported")
    assert exc.value.code == 1  # Still fails even with --allow-unsupported
    capsys.readouterr()  # consume output
    # The task with host requirements should report unsupported_by="host-probe"
    doc = json.loads((tmp_path / "a/check/check.json").read_text())
    race = next(t for t in doc["tasks"] if t["task"] == "fix_race")
    assert race["verdict"] == "unsupported" and race["unsupported_by"] == "host-probe"


def test_run_exits_1_when_the_probe_failed_and_still_runs_unconstrained_tasks(tmp_path, monkeypatch, capsys):
    root = _repo(tmp_path)
    out = tmp_path / "runs"
    with pytest.raises(SystemExit) as exc:
        _run(root, out, HostRunner(None), monkeypatch, "--task", "fix_add", "--task", "fix_race")
    assert exc.value.code == 1  # not a green run with the constrained task never graded
    logged = capsys.readouterr()
    assert "2 ran, 0 already done, 0 crashed" in logged.out
    assert "the host probe failed, so these tasks have cells that were not run: fix_race" in logged.err
    # A probe failure says nothing about the cells: no marker, so report counts them missing, not unsupported.
    assert summarize(out / "b1")["unsupported"] == []
    assert len(summarize(out / "b1")["missing"]) == 2


def test_a_complete_batch_rerun_on_a_failed_probe_exits_0(tmp_path, monkeypatch, capsys):
    """Regression: cells that already have a result are not left unrun, so a re-run of a finished
    batch with a failing probe is not a failure (the same as a hardware miss)."""
    root = _repo(tmp_path)
    out = tmp_path / "runs"
    _run(root, out, HostRunner(_probe_out(affinity=8)), monkeypatch, "--task", "fix_add", "--task", "fix_race")
    capsys.readouterr()
    _run(root, out, HostRunner(None), monkeypatch, "--task", "fix_add", "--task", "fix_race")  # no SystemExit
    logged = capsys.readouterr()
    assert "0 ran, 4 already done, 0 crashed" in logged.out and "host probe failed" not in logged.err
    assert summarize(out / "b1")["unsupported"] == [] and summarize(out / "b1")["missing"] == []


def test_a_partly_finished_batch_rerun_on_a_failed_probe_exits_1(tmp_path, monkeypatch, capsys):
    """The unfinished cell of a task still makes the re-run fail, even though its other cell is done."""
    root = _repo(tmp_path)
    out = tmp_path / "runs"
    _run(root, out, HostRunner(_probe_out(affinity=8)), monkeypatch, "--task", "fix_add", "--task", "fix_race")
    capsys.readouterr()
    race_cells = sorted((out / "b1").glob("fix_race__*/result.json"))
    assert len(race_cells) == 2
    race_cells[0].unlink()  # one fix_race cell is left unfinished
    with pytest.raises(SystemExit) as exc:
        _run(root, out, HostRunner(None), monkeypatch, "--task", "fix_add", "--task", "fix_race")
    assert exc.value.code == 1
    assert "host probe failed" in capsys.readouterr().err
    assert race_cells[1].is_file()  # the finished cell keeps its result


def test_an_unfinished_cell_of_another_task_does_not_fail_a_task_that_is_done(tmp_path, monkeypatch, capsys):
    """fix_race is complete; only a fix_add cell is left, so the failed probe costs nothing."""
    root = _repo(tmp_path)
    out = tmp_path / "runs"
    _run(root, out, HostRunner(_probe_out(affinity=8)), monkeypatch, "--task", "fix_add", "--task", "fix_race")
    capsys.readouterr()
    add_cells = sorted((out / "b1").glob("fix_add__*/result.json"))
    add_cells[0].unlink()
    _run(root, out, HostRunner(None), monkeypatch, "--task", "fix_add", "--task", "fix_race")  # exits 0
    assert "host probe failed" not in capsys.readouterr().err


def test_a_probe_failure_never_unsupports_a_task_with_no_constraint():
    assert unmet(_task({}), {"error": "host probe did not start"}) == (None, None)


def test_run_batch_refuses_a_bad_host_value_before_probing_or_running(tmp_path):
    runner = HostRunner(_probe_out())
    ctx = EvalContext(manifest=parse_manifest(MANIFEST), git_dir=tmp_path, content_dir=tmp_path, image="img:1",
                      runner=runner)
    with pytest.raises(HostSpecError, match="min_cpus must be an integer"):
        run_batch(ctx, [_task({"min_cpus": 0})], ["guidance:none"], [], 1, tmp_path / "batch")
    assert runner.specs == [] and runner.probes == []


def test_marking_a_cell_unsupported_clears_what_an_unfinished_run_left(tmp_path):
    cell_dir = tmp_path / "t__arm__m__r1"
    (cell_dir / "agent").mkdir(parents=True)
    (cell_dir / "agent" / "stale.txt").write_text("from an earlier run", encoding="utf-8")
    opts = SimpleNamespace(model="m", profile=SimpleNamespace(name="p"))
    _mark_unsupported(cell_dir, (_task(None), "arm", opts, 1), "needs 4 CPUs", {"cpus": 2})
    assert sorted(p.name for p in cell_dir.iterdir()) == [UNSUPPORTED_FILE]


def test_load_unsupported_skips_a_cell_that_has_a_result(tmp_path):
    for name, has_result in (("done__arm__m__r1", True), ("open__arm__m__r1", False)):
        (tmp_path / name).mkdir()
        (tmp_path / name / UNSUPPORTED_FILE).write_text(
            json.dumps({"task": "t", "arm": "arm", "model": "m", "reason": "r"}), encoding="utf-8")
        if has_result:
            (tmp_path / name / "result.json").write_text("{}", encoding="utf-8")
    assert [c["cell"] for c in load_unsupported(tmp_path)] == ["open__arm__m__r1"]


# ------------------------------------------------------------------ review round: vlad-perevezentsev


def test_a_host_miss_still_checks_the_oracle_when_only_cpus_are_short(tmp_path, monkeypatch, capsys):
    typo = dict(RACE, gate=["tests_pas"], host={"min_cpus": 4})  # a broken grader must not hide behind the host
    root, _ = make_repo(tmp_path, {"fix_race": typo})
    with pytest.raises(SystemExit) as exc:
        _check(root, tmp_path / "a", HostRunner(_probe_out(affinity=2)), monkeypatch, "--allow-unsupported",
               "--task", "fix_race")
    assert exc.value.code == 1
    race = json.loads((tmp_path / "a/check/check.json").read_text())["tasks"][0]
    assert race["verdict"] == "broken" and "oracle" in race and "null" not in race


def test_a_host_miss_still_probes_isolation_and_skips_only_null(tmp_path, monkeypatch, capsys):
    root = _repo(tmp_path, host={"min_cpus": 4})
    _check(root, tmp_path / "a", HostRunner(_probe_out(affinity=2)), monkeypatch, "--task", "fix_race",
           "--allow-unsupported")
    race = json.loads((tmp_path / "a/check/check.json").read_text())["tasks"][0]
    assert race["verdict"] == "unsupported" and race["unsupported_by"] == "host"
    assert "isolation" in race and "oracle" in race and "null" not in race


def test_warm_does_not_judge_the_host(tmp_path, monkeypatch, capsys):
    root = _repo(tmp_path)
    runner = HostRunner(_probe_out(affinity=2, cpuinfo=AMD_CPUINFO))
    _check(root, tmp_path / "a", runner, monkeypatch, "--task", "fix_race", "--warm")
    race = json.loads((tmp_path / "a/check/check.json").read_text())["tasks"][0]
    assert race["verdict"] != "unsupported" and "null" in race and race["warm"] is True


def test_a_probe_failure_leaves_no_marker_for_report_to_count(tmp_path, monkeypatch, capsys):
    root = _repo(tmp_path)
    out = tmp_path / "runs"
    with pytest.raises(SystemExit):
        _run(root, out, HostRunner(None), monkeypatch, "--task", "fix_race")
    assert not list((out / "b1").glob("*/unsupported.json"))
    assert summarize(out / "b1")["unsupported"] == []


def test_validate_reports_a_bad_host_shape(tmp_path, monkeypatch, capsys):
    root, _ = make_repo(tmp_path, {"fix_race": dict(RACE, host={"min_cpus": "4", "cpu_vendors": ["Intel"]})})
    with pytest.raises(SystemExit) as exc:
        _cli(["repo", "validate", f"{root}@HEAD", "--manifest", ".agent-eval/repo-eval.yaml"], monkeypatch, HostRunner(None))
    assert exc.value.code == 1
    err = capsys.readouterr()
    assert "host.min_cpus must be an integer >= 1" in err.out + err.err


def test_missing_cpuinfo_is_a_probe_failure_not_a_miss():
    blind = host_facts(_probe_out(affinity=8, cpuinfo=""))
    assert blind["cpu_vendor"] is None and blind["cpu_flags"] == []
    assert unmet(_task({"cpu_vendors": ["GenuineIntel"]}), blind)[1] == "host-probe"
    assert unmet(_task({"cpu_flags": ["avx2"]}), blind)[1] == "host-probe"
    assert unmet(_task({"min_cpus": 4}), blind) == (None, None)  # CPUs alone do not need cpuinfo


def test_vendors_compare_in_any_case_and_unknown_names_are_refused():
    intel = host_facts(_probe_out())
    assert unmet(_task({"cpu_vendors": ["genuineintel"]}), intel) == (None, None)
    with pytest.raises(HostSpecError, match="unknown vendor.*Intel"):
        requirements(_task({"cpu_vendors": ["Intel"]}))
