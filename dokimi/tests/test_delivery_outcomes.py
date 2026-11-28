# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
"""Which delivery failures reach the verifier and which are missing measurements."""

import argparse
import json
import shutil

import pytest

from dokimi import process
from dokimi.harnesses import delivery
from tests.fixtures.delivery.build import build

real_run = process.run
real_container = delivery.container
needs_sh = pytest.mark.skipif(not shutil.which("sh"), reason="requires a POSIX shell")


@pytest.fixture
def pack(tmp_path):
    build(tmp_path, "sha256:" + "a" * 64)
    return argparse.Namespace(
        mode="release",
        manifest=tmp_path / "good.json",
        tasks=tmp_path / "tasks.json",
        policy=tmp_path / "policy.json",
        corpus=tmp_path / "corpus.json",
        model=None,
        provider=None,
    )


@pytest.fixture
def local_shell(monkeypatch):
    """Run the container() logic with a local sh standing in for docker run."""

    def docker(argv, input=None, env=None, timeout=None):
        if argv[1] != "run":
            return process.reply()
        return real_run(["sh", "-c", argv[-1]], input=input, env=env, timeout=timeout)

    def local(image, script, mounts, *args, **kwargs):
        for name, path in mounts.items():
            script = script.replace(f"/{name}", str(path))
        return real_container(image, script, {}, *args, **kwargs)

    monkeypatch.setattr(process, "run", docker)
    monkeypatch.setattr(delivery, "container", local)


@needs_sh
@pytest.mark.parametrize(
    "script, status", [("exit 125", 125), ("exit 137", 137), ("sh -c 'kill -9 $$'", 137)]
)
def test_consumer_exit_status_reaches_the_verifier(pack, local_shell, script, status):
    manifest = json.loads(pack.manifest.read_text())
    verify = f'[ "$consumer_exit" -eq {status} ] && exit 10\nexit 99'
    row: dict = {}
    assert delivery._grade(manifest, b"", b"", script, b"", verify, 10, row) == "FAIL"
    assert row["evidence"]["exit"] == status and row["evidence"]["verifier_exit"] == 10
    assert "delivery-consumer-" not in row["evidence"]["stderr"]


@needs_sh
def test_wrong_answer_that_exits_125_scores_zero(pack, local_shell):
    pack.mode = "scan"
    catalog = json.loads(pack.tasks.read_text())
    catalog["tasks"]["double"]["fixture"] = {"default": {"script": "exit 125", "claimed_success": True}}
    pack.tasks.write_text(json.dumps(catalog))
    summary = delivery.run(pack)["summary"]
    assert summary["A"]["score"] == 0 and summary["A"]["coverage"] == [2, 2]
    assert summary["A"]["false_success"] == [2, 2] and not summary["partial"]


@pytest.mark.parametrize("status", [125, 137])
def test_runner_failure_before_the_consumer_finishes_is_inconclusive(pack, monkeypatch, status):
    calls = []

    def docker(argv, **kwargs):
        calls.append(argv)
        return process.reply(returncode=status, stderr="docker: Error response from daemon\n")

    monkeypatch.setattr(process, "run", docker)
    row = delivery.run(pack)["observations"][0]
    assert row["outcome"] == "INCONCLUSIVE" and "Error response from daemon" in row["reason"]
    assert not any(argv[-1].startswith("consumer_exit=") for argv in calls)


@pytest.mark.parametrize(
    "answer, exit_code, failure",
    [(dict(timed_out=True), None, "timeout"), (dict(returncode=137), 137, "runner")],
)
def test_failed_consumer_container_keeps_the_answer_and_its_output(pack, monkeypatch, answer, exit_code, failure):
    pack.mode = "scan"
    catalog = json.loads(pack.tasks.read_text())
    catalog["tasks"]["double"]["fixture"] = {"default": {"script": "echo partial; sleep 99"}}
    pack.tasks.write_text(json.dumps(catalog))
    monkeypatch.setattr(process, "run", lambda argv, **kw: process.reply("partial\n", "late\n", **answer))
    row = delivery.run(pack)["observations"][0]
    assert row["outcome"] == "INCONCLUSIVE" and row["reason"] == "ValueError: late\n"
    assert row["evidence"] == {
        "script": "echo partial; sleep 99",
        "exit": exit_code,
        "stderr": "late\n",
        "stdout": "partial\n",
        "verifier_exit": None,
        "failure": failure,
    }
