# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
"""Contracts and paired replay from docs/delivery.md."""

import argparse
import json
import os
import re
import shutil
from pathlib import Path

import pytest

from dokimi import process
from dokimi.harnesses import delivery
from dokimi.report.delivery import render
from dokimi.gate.delivery import evaluate, score
from tests.fixtures.delivery.build import build


@pytest.fixture
def pack(tmp_path):
    build(tmp_path, os.environ.get("DOKIMI_DELIVERY_IMAGE", "sha256:" + "a" * 64))
    return argparse.Namespace(
        mode="release",
        manifest=tmp_path / "good.json",
        tasks=tmp_path / "tasks.json",
        policy=tmp_path / "policy.json",
        corpus=tmp_path / "corpus.json",
        model=None,
        provider=None,
    )


def test_mixed_observations():
    identity = {"profile": "cpu", "policy_version": "2", "artifact": "rc"}
    passed = dict(
        task="double",
        outcome="PASS",
        origin="contract",
        arm="release",
        identity=identity,
        evidence={"verifier_exit": 0},
    )
    rows = [
        passed,
        dict(passed, origin="scan", severity="critical", outcome="FAIL"),
        dict(passed, identity=dict(identity, artifact="old"), outcome="FAIL"),
    ]
    assert evaluate(["double"], identity, rows)["verdict"] == "PASS"
    assert evaluate(["double", "install"], identity, rows)["verdict"] == "INCONCLUSIVE"
    assert evaluate(["double"], identity, rows[1:])["verdict"] == "INCONCLUSIVE"
    assert evaluate(["double"], identity, [passed, passed])["verdict"] == "INCONCLUSIVE"


def test_release_never_loads_scan(pack, monkeypatch):
    monkeypatch.setattr(
        delivery, "container", lambda *args, **kwargs: process.reply("cpu:34\ncpu:-8\n")
    )
    pack.corpus.unlink()
    assert delivery.run(pack)["summary"]["verdict"] == "PASS"
    pack.manifest.with_name("good.cli").write_text("changed")
    result = delivery.run(pack)
    assert result["summary"]["verdict"] == "INCONCLUSIVE"
    assert "digest mismatch" in result["observations"][0]["reason"]


def test_missing_runner(pack, monkeypatch):
    def unavailable(*args, **kwargs):
        raise OSError("no GPU runner")

    monkeypatch.setattr(delivery, "container", unavailable)
    result = delivery.run(pack)
    assert result["summary"]["verdict"] == "INCONCLUSIVE"
    assert result["observations"][0]["evidence"] is None
    pack.mode = "scan"
    result = delivery.run(pack)["summary"]
    assert result["partial"] and result["A"]["score"] is None and result["B"]["coverage"] == [0, 2]


def test_verifier_error(pack, monkeypatch):
    def grade(image, script, mounts, *args, **kwargs):
        return process.reply(returncode=0 if mounts else 2)

    monkeypatch.setattr(delivery, "container", grade)
    row = delivery.run(pack)["observations"][0]
    assert row["outcome"] == "INCONCLUSIVE" and row["evidence"]["verifier_exit"] == 2


def test_group_weights_and_missing_repeat():
    tasks = {"x": {"group": "one"}, "y": {"group": "one"}, "z": {"group": "two"}}
    rows = [
        dict(task=t, arm="A", outcome=v)
        for t, v in zip("xxyyzz", ["PASS", "FAIL", "FAIL", "FAIL", "PASS", "PASS"], strict=True)
    ]
    scores = score({"one": 3, "two": 1}, tasks, 2, rows, "A")
    assert scores["score"] == 4.375 and scores["coverage"] == [6, 6]
    assert score({"one": 3, "two": 1}, tasks, 2, rows[:-1], "A")["score"] is None


@pytest.mark.skipif(
    not os.environ.get("DOKIMI_DELIVERY_IMAGE"), reason="requires a preloaded Docker runtime image"
)
def test_real_delivery(pack):
    assert delivery.run(pack)["summary"]["verdict"] == "PASS"
    for name in ("bad-example", "broken"):
        pack.manifest = pack.manifest.with_name(name + ".json")
        result = delivery.run(pack)["summary"]
        assert result["verdict"] == "FAIL"
        assert result["checks"]["binary-double"] == ("PASS" if name == "bad-example" else "FAIL")
    pack.manifest = pack.manifest.with_name("good.json")
    pack.mode = "scan"
    summary = delivery.run(pack)["summary"]
    assert summary["A"]["score"] == 10 and summary["B"]["false_success"] == [2, 2]
    assert summary["paired"]["double"]["regressions"] == 2
    assert summary["paired"]["double"]["consistent_change_across_repeats"]
    corpus = json.loads(pack.corpus.read_text())
    old_source = corpus["sources"][0]
    workaround = "shipcalc twice"
    tasks = json.loads(pack.tasks.read_text())
    key = delivery.digest(("shipcalc double\n" + workaround).encode())
    tasks["tasks"]["double"]["fixture"][key] = {"script": workaround, "claimed_success": True}
    pack.tasks.write_text(json.dumps(tasks))
    policy = json.loads(pack.policy.read_text())
    policy["profiles"]["linux-cpu"]["tasks_sha256"] = delivery.digest(pack.tasks.read_bytes())
    pack.policy.write_text(json.dumps(policy))
    (pack.corpus.parent / old_source["path"]).write_text(workaround)
    old_source.update(sha256=delivery.digest(workaround.encode()))
    pack.corpus.write_text(json.dumps(corpus))
    pack.manifest = pack.manifest.with_name("broken.json")
    assert delivery.run(pack)["summary"]["B"]["score"] == 10
    pack.mode = "release"
    assert delivery.run(pack)["summary"]["verdict"] == "FAIL"
    pack.manifest = pack.manifest.with_name("good.json")
    pack.mode = "scan"
    Path(pack.corpus.parent, corpus["sources"][0]["path"]).unlink()
    summary = delivery.run(pack)["summary"]
    assert summary["A"]["score"] == 10 and summary["B"]["score"] is None


def test_live_adapter_and_offline_report(pack, monkeypatch, capsys):
    from dokimi import llm
    from dokimi.cli import parse_args
    from dokimi.metrics.usage import UsageRecord

    def answer(prompt, *args, **kwargs):
        payload = json.loads(prompt)
        assert "17" not in prompt and "cpu:34" not in prompt
        if len(payload["sources"]) == 2:
            raise RuntimeError("provider unavailable")
        return '```json\n{"script":"shipcalc double","claimed_success":true}\n```', UsageRecord(
            cost_usd=0.01
        )

    monkeypatch.setattr(llm, "llm_call_with_usage", answer)
    monkeypatch.setattr(delivery, "container", lambda *args, **kwargs: process.reply())
    out = pack.manifest.with_name("observations.json")
    for argv in (
        [
            "scan",
            "--model",
            "model-2026",
            "--tasks",
            str(pack.tasks),
            "--manifest",
            str(pack.manifest),
            "--corpus",
            str(pack.corpus),
            "--out",
            str(out),
        ],
        ["report", str(out)],
    ):
        args = parse_args(["delivery", *argv, "--json"])
        with pytest.raises(SystemExit) as exc:
            args.func(args)
        assert exc.value.code == 3
        envelope = json.loads(capsys.readouterr().out)
        assert envelope["status"] == "partial" and envelope["data"]["summary"]["A"]["score"] == 10
        assert envelope["data"]["summary"]["B"]["score"] is None
        assert envelope["cost"]["spent_usd"] == 0.02
        report = render(envelope["data"])
        assert (
            "provider unavailable" in report and "partial" in report and "article author" in report
        )
        monkeypatch.setattr(
            llm, "llm_call_with_usage", lambda *a, **k: pytest.fail("report called a model")
        )


def test_container_protocol(monkeypatch):
    monkeypatch.setenv("DOCKER_CONTEXT", "selected")
    calls = []

    def timeout(argv, **kwargs):
        assert kwargs["env"].resolve()["DOCKER_CONTEXT"] == "selected"
        calls.append(argv)
        return process.reply(stderr="deadline", timed_out=True)

    monkeypatch.setattr(process, "run", timeout)
    with pytest.raises(ValueError, match="deadline"):
        delivery.container("image", "true", {}, b"", 1, gpu=True)
    assert calls[0][:2] == ["docker", "run"] and "--gpus" in calls[0]
    assert calls[0][calls[0].index("--network") + 1] == "none"
    assert calls[0][calls[0].index("--cpus") + 1] == "2"
    assert calls[0][calls[0].index("--memory") + 1] == "1g"
    assert "--user" not in calls[0]
    assert calls[1][:3] == ["docker", "rm", "-f"]


@pytest.mark.parametrize("failure", [KeyboardInterrupt(), RuntimeError("client interrupted")])
def test_container_removes_its_name_after_an_exception(monkeypatch, failure):
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        if argv[1] == "run":
            raise failure
        raise OSError("cleanup unavailable")

    monkeypatch.setattr(process, "run", run)
    with pytest.raises(type(failure)) as caught:
        delivery.container("image", "true", {}, b"", 1)
    assert caught.value is failure
    assert len(calls) == 2
    assert calls[1] == ["docker", "rm", "-f", calls[0][calls[0].index("--name") + 1]]


def test_mounted_starter_preserves_the_pinned_bytes_on_windows(pack, monkeypatch):
    write_text = Path.write_text

    def windows_write_text(path, data, *args, **kwargs):
        if path.name == "initial":
            kwargs["newline"] = "\r\n"
        return write_text(path, data, *args, **kwargs)

    def container(image, script, mounts, *args, **kwargs):
        if mounts:
            original = (pack.tasks.parent / "consumer.sh").read_bytes()
            assert mounts["initial"].read_bytes() == original
        return process.reply()

    monkeypatch.setattr(Path, "write_text", windows_write_text)
    monkeypatch.setattr(delivery, "container", container)
    for row in delivery.run(pack)["observations"]:
        assert row["outcome"] == "PASS", row["reason"]
        assert row["initial_sha256"] == delivery.digest((pack.tasks.parent / "consumer.sh").read_bytes())


@pytest.mark.skipif(not os.environ.get("DOKIMI_DELIVERY_IMAGE"), reason="requires a preloaded Docker runtime image")
def test_consumer_can_modify_its_starter(pack):
    manifest = json.loads(pack.manifest.read_text())
    original_path = pack.manifest.parent / manifest["docs"]["path"]
    original_path.write_bytes(b"printf 'shipcalc double\\n' > consumer.sh && sh consumer.sh")
    manifest["docs"]["sha256"] = delivery.digest(original_path.read_bytes())
    pack.manifest.write_text(json.dumps(manifest))
    row = delivery.run(pack)["observations"][0]
    assert row["outcome"] == "PASS", row["evidence"]


@pytest.mark.skipif(not shutil.which("sh"), reason="requires a POSIX shell")
def test_multiline_installer_keeps_its_streams_separate_from_the_consumer(pack, monkeypatch):
    manifest = json.loads(pack.manifest.read_text())
    manifest["install"] = "cat >/dev/null\nprintf 'installing\\n'\n" + manifest["install"] + "\nprintf 'installed\\n'"
    pack.manifest.write_text(json.dumps(manifest))

    def container(image, script, mounts, data, *args, **kwargs):
        if mounts:
            for name, path in mounts.items():
                script = script.replace(f"/{name}", str(path))
        return process.run(["sh", "-c", script], input=data, env=process.Env.inherit(), timeout=10)

    monkeypatch.setattr(delivery, "container", container)
    row = delivery.run(pack)["observations"][0]
    assert row["outcome"] == "PASS", row["evidence"]
    assert row["evidence"]["stdout"] == "cpu:34\ncpu:-8\n"
    assert row["evidence"]["stderr"] == "installing\ninstalled\n"


@pytest.mark.skipif(not os.environ.get("DOKIMI_DELIVERY_IMAGE"), reason="requires a preloaded Docker runtime image")
def test_installer_cannot_drain_the_hidden_input(pack):
    manifest = json.loads(pack.manifest.read_text())
    manifest["install"] = "cat >/dev/null\n" + manifest["install"]
    pack.manifest.write_text(json.dumps(manifest))

    rows = delivery.run(pack)["observations"]
    assert [row["outcome"] for row in rows] == ["PASS", "PASS"], rows
    assert all(row["evidence"]["stdout"] == "cpu:34\ncpu:-8\n" for row in rows)


def test_offline_release_report_rechecks_evidence(pack, monkeypatch, capsys):
    from dokimi.cli import parse_args

    monkeypatch.setattr(delivery, "container", lambda *args, **kwargs: process.reply())
    out = pack.manifest.with_name("result.json")
    args = parse_args(
        [
            "delivery",
            "release",
            "--manifest",
            str(pack.manifest),
            "--tasks",
            str(pack.tasks),
            "--policy",
            str(pack.policy),
            "--out",
            str(out),
            "--json",
        ]
    )
    args.func(args)
    record = json.loads(capsys.readouterr().out)["data"]
    assert record["summary"]["verdict"] == "PASS"
    assert "binary-double · PASS" in render(record)
    record["observations"][0]["origin"] = "scan"
    out.write_text(json.dumps(record))
    args = parse_args(["delivery", "report", str(out), "--json"])
    with pytest.raises(SystemExit) as exc:
        args.func(args)
    assert exc.value.code == 3
    assert json.loads(capsys.readouterr().out)["verdict"] == "inconclusive"


@pytest.mark.parametrize(
    "mode, summary, exit_code, text",
    [
        ("release", {"verdict": "INCONCLUSIVE", "checks": {}}, 3, ("**INCONCLUSIVE**",)),
        (
            "scan",
            {"A": {}, "B": {}, "partial": True},
            3,
            ("Source attribution unavailable.", "Paired evidence unavailable."),
        ),
        (
            "scan",
            {
                "A": {"score": "unknown", "coverage": [1], "groups": {"tools": {"scenarios": {"one": {}}}}},
                "B": {"score": 10, "coverage": [2, 2], "false_success": [0, 2], "groups": []},
                "partial": False,
                "attribution": {"sources": [None, {"url": "https://example.test"}]},
                "paired": {"one": {"measured_pairs": 2}, "two": None},
            },
            0,
            (
                "| B | 10.00 | 2/2 | 0/2 |",
                "| A | tools | — | one | —/— |",
                "one. 2/— pairs measured",
                "https://example.test · Capture date unavailable",
            ),
        ),
    ],
)
def test_offline_reports_accept_schema_valid_partial_evidence(tmp_path, capsys, mode, summary, exit_code, text):
    from dokimi.cli import parse_args

    data = {
        "schema_version": "delivery.v1",
        "mode": mode,
        "identity": dict.fromkeys(("manifest", "artifact", "profile", "catalog", "image", "version"), "fixture"),
        "summary": summary,
        "observations": [],
        "required": ["check"],
        "date": "2026-11-25",
        "reproduce": "dokimi delivery report fixture.json",
    }
    path = tmp_path / "result.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    for flags in (["--json"], []):
        args = parse_args(["delivery", "report", str(path), *flags])
        if exit_code:
            with pytest.raises(SystemExit) as exit_info:
                args.func(args)
            assert exit_info.value.code == exit_code
        else:
            args.func(args)
        output = capsys.readouterr().out
        if flags:
            assert json.loads(output)["data"]["mode"] == mode
        else:
            assert all(fragment in output for fragment in text)


@pytest.mark.parametrize(
    ("amounts", "spent", "priced"),
    [
        (("unknown", 0.02), 0.02, False),
        ((True, 0.02), 0.02, False),
        ((-0.1, 0.02), 0.02, False),
        ((10**400, 0.02), 0.02, False),
        ((float("inf"), 0.02), 0.02, False),
        ((1e308, 1e308), 1e308, False),
        ((None, 0.02), 0.02, False),
        ((0, 0.02), 0.02, True),
    ],
)
def test_offline_reports_keep_known_spend_from_schema_valid_observations(
    tmp_path, capsys, amounts, spent, priced
):
    from dokimi import artifacts
    from dokimi.cli import parse_args

    fixture = Path(__file__).parent / "fixtures" / "artifacts" / "delivery.v1.json"
    data = json.loads(fixture.read_text(encoding="utf-8"))
    for row, amount in zip(data["observations"], amounts, strict=True):
        row.update(model_calls=1, cost_usd=amount)
    path = tmp_path / "delivery.json"
    # Valid JSON numeric syntax can still exceed the host's float range.
    path.write_text(json.dumps(data).replace("Infinity", "1e999"), encoding="utf-8")
    assert artifacts.load_artifact("delivery", path)["mode"] == "release"

    args = parse_args(["delivery", "report", str(path)])
    args.func(args)
    assert "**PASS**" in capsys.readouterr().out

    args = parse_args(["delivery", "report", str(path), "--json"])
    args.func(args)
    output = capsys.readouterr().out
    assert "Infinity" not in output
    cost = json.loads(output)["cost"]
    assert (cost["calls"], cost["spent_usd"], cost["priced"]) == (2, spent, priced)


def test_claim_without_grader_is_visible(pack, monkeypatch):
    pack.mode = "scan"

    def run_container(image, script, mounts, *a, **kw):
        return process.reply(returncode=0 if mounts else 2)

    monkeypatch.setattr(delivery, "container", run_container)
    stats = delivery.run(pack)["summary"]["B"]
    assert stats["false_success"] == [0, 2] and stats["unverified_claims"] == 2
    assert stats["score"] is None


@pytest.mark.skipif(
    not os.environ.get("DOKIMI_DELIVERY_IMAGE"), reason="requires a preloaded Docker runtime image"
)
def test_promote_reproduced_observation(pack):
    pack.manifest = pack.manifest.with_name("zero-rc.json")
    assert delivery.run(pack)["summary"]["verdict"] == "PASS"
    pack.tasks = pack.tasks.with_name("regression-tasks.json")
    pack.corpus = pack.corpus.with_name("zero-corpus.json")
    pack.mode = "scan"
    scan = delivery.run(pack)
    (source,) = scan["summary"]["attribution"]["sources"]
    assert scan["summary"]["paired"]["double-zero"]["attempts"] == [["FAIL", "FAIL"]] * 2
    pack.mode = "release"
    pack.policy = pack.policy.with_name("regression-policy.json")
    pack.corpus.unlink()
    result = delivery.run(pack)
    (regression,) = [r for r in result["observations"] if r["task"] == "double-zero"]
    assert regression["finding_id"] == source["finding_id"] == "missing-zero-row"
    assert result["summary"]["checks"] == {
        "double": "PASS",
        "binary-double": "PASS",
        "double-zero": "FAIL",
    }


@pytest.mark.parametrize(
    "argv,missing",
    [
        (["delivery"], "mode"),
        (
            [
                "delivery",
                "release",
                "--json",
                "--tasks",
                "tasks",
                "--policy",
                "policy",
                "--out",
                "run",
            ],
            "--manifest",
        ),
        (
            [
                "delivery",
                "scan",
                "--manifest",
                "rc",
                "--tasks",
                "tasks",
                "--corpus",
                "sources",
                "--out",
                "run",
            ],
            "--fixture",
        ),
    ],
)
def test_required_delivery_inputs(argv, missing, capsys, monkeypatch):
    import sys
    from dokimi.cli import main

    monkeypatch.setattr(sys, "argv", ["dokimi", *argv])
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 2
    out = capsys.readouterr()
    if "--json" in argv:
        assert missing in json.loads(out.out)["error"]["message"]
    else:
        assert missing in out.err


def test_priced_pairs_count_only_their_own_claims(pack, monkeypatch, capsys):
    from dokimi.cli import parse_args
    from dokimi import llm
    from dokimi.metrics.usage import UsageRecord

    def reply(prompt, *a, **kw):
        claims_success = len(json.loads(prompt)["sources"]) == 1
        return json.dumps({"script": "false", "claimed_success": claims_success}), UsageRecord(
            cost_usd=0.01
        )

    monkeypatch.setattr(llm, "llm_call_with_usage", reply)
    monkeypatch.setattr(delivery, "container", lambda *a, **kw: process.reply(returncode=10))
    args = parse_args(
        [
            "delivery",
            "scan",
            "--model",
            "test-model",
            "--tasks",
            str(pack.tasks),
            "--manifest",
            str(pack.manifest),
            "--corpus",
            str(pack.corpus),
            "--out",
            str(pack.manifest.parent / "run.json"),
            "--json",
        ]
    )
    args.func(args)
    envelope = json.loads(capsys.readouterr().out)
    assert envelope["cost"]["priced"] is True and envelope["cost"]["calls"] == 4
    assert envelope["data"]["summary"]["A"]["false_success"] == [2, 2]
    assert envelope["data"]["summary"]["B"]["false_success"] == [0, 0]


def test_reply_errors_visible_in_offline_report(pack, monkeypatch):
    from dokimi import llm
    from dokimi.metrics.usage import UsageRecord

    def answer(prompt, *args, **kwargs):
        text = "no answer" if len(json.loads(prompt)["sources"]) == 1 else '{"script": 7}'
        return text, UsageRecord(cost_usd=0.01)

    monkeypatch.setattr(llm, "llm_call_with_usage", answer)
    monkeypatch.setattr(delivery, "container", lambda *a, **kw: process.reply())
    pack.mode, pack.model, pack.fixture = "scan", "test-model", False
    data = delivery.run(pack)
    assert data["identity"]["harness"] == "litellm-shell-v2"
    assert all(row["outcome"] == "FAIL" for row in data["observations"])
    report = render(data)
    assert "model reply contains no usable JSON object" in report
    assert "answer has no string script field" in report


def test_failed_workspace_setup_is_inconclusive(pack, monkeypatch):
    def prepare(image, script, mounts, *args, **kwargs):
        assert mounts, "verifier must not run after failed setup"
        marker = re.search(r"delivery-workspace-[0-9a-f]+", script)
        assert marker
        return process.reply(returncode=121, stderr=f"workspace unavailable\n{marker[0]}\n")

    monkeypatch.setattr(delivery, "container", prepare)
    row = delivery.run(pack)["observations"][0]
    assert row["outcome"] == "INCONCLUSIVE"
    assert "workspace unavailable" in row["reason"]
    assert row["evidence"]["verifier_exit"] is None
    assert row["evidence"]["stderr"] == "workspace unavailable\n"


def test_consumer_exit_121_reaches_verifier(pack, monkeypatch):
    def run(image, script, mounts, *args, **kwargs):
        if mounts:
            return process.reply(returncode=121, stderr="consumer failed")
        assert script.startswith("consumer_exit=121\n")
        return process.reply(returncode=10)

    monkeypatch.setattr(delivery, "container", run)
    row = delivery.run(pack)["observations"][0]
    assert row["outcome"] == "FAIL"
    assert row["evidence"]["verifier_exit"] == 10


def test_scan_rejects_empty_corpus(pack):
    pack.mode = "scan"
    pack.corpus.write_text('{"sources": []}')
    with pytest.raises(delivery.InputError, match="at least one source"):
        delivery.run(pack)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("tasks", [], "catalog tasks must be an object"),
        ("tasks", {"double": []}, "task definitions must be objects"),
        ("weights", [], "catalog weights must be an object"),
        ("weights", {"linux-cpu": []}, "scan weights for linux-cpu must be an object"),
        ("weights", {"linux-cpu": {"operations": True}}, "scan needs positive weights"),
    ],
)
def test_scan_with_malformed_catalog_is_an_input_error(pack, capsys, monkeypatch, field, value, message):
    import sys
    from dokimi.cli import main

    catalog = json.loads(pack.tasks.read_text())
    catalog[field] = value
    pack.tasks.write_text(json.dumps(catalog))
    monkeypatch.setattr(delivery, "container", lambda *a, **kw: pytest.fail("used Docker on invalid input"))
    monkeypatch.setattr(sys, "argv", [
        "dokimi", "delivery", "scan", "--manifest", str(pack.manifest), "--tasks", str(pack.tasks),
        "--corpus", str(pack.corpus), "--fixture", "--out", str(pack.manifest.with_name("scan.json")), "--json",
    ])
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 2
    error = json.loads(capsys.readouterr().out)["error"]
    assert message in error["message"]


def test_truncated_model_output_is_inconclusive(pack, monkeypatch):
    from dokimi import llm
    from dokimi.metrics.usage import UsageRecord

    monkeypatch.setattr(
        llm, "llm_call_with_usage",
        lambda *a, **kw: ('{"script":"true"}', UsageRecord(cost_usd=0.01, truncated=True)),
    )
    monkeypatch.setattr(delivery, "container", lambda *a, **kw: pytest.fail("ran a truncated response"))
    pack.mode, pack.model = "scan", "test-model"
    data = delivery.run(pack)
    assert data["summary"]["partial"]
    for row in data["observations"]:
        assert row["outcome"] == "INCONCLUSIVE"
        assert "model reply was truncated" in row["reason"]
        assert row["cost_usd"] == 0.01
        assert row["evidence"] is None
