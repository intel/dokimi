# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Pin which gate kind each caller reads, end to end."""
import argparse
import json
from unittest.mock import patch

import yaml

from dokimi.commands.run import cmd_run
from dokimi.gate.score_gate import check_gate
from dokimi.runner.run import run_benchmark

SPEC = {
    "weights": {"coverage": 0.4, "freshness_lite": 0.3, "readability": 0.3},
    "metrics": {"freshness_lite": {"max_age_days": 365}, "readability": {"grade_max": 12}},
}


def _patches(root):
    return [
        patch("dokimi.runner.run.discover_markdown", return_value=[root / "docs/a.md"]),
        patch("dokimi.runner.run.load_docs", return_value={str(root / "docs/a.md"): "hello world"}),
        patch("dokimi.runner.run.chunk_text", return_value=["c1"]),
        patch("dokimi.runner.run.coverage.score", return_value=0.8),
        patch("dokimi.runner.run.freshness_lite.score", return_value=0.9),
        patch("dokimi.runner.run.readability.score", return_value=0.7),
    ]


def _run(tmp_path, future, strict):
    spec = dict(SPEC, future=future)
    sp = tmp_path / "spec.yaml"
    sp.write_text(yaml.safe_dump(spec))
    args = argparse.Namespace(root=str(tmp_path), spec=str(sp), out_json=str(tmp_path / "o.json"),
                              out_md=str(tmp_path / "o.md"), strict=strict)
    ps = _patches(tmp_path)
    for p in ps:
        p.start()
    try:
        cmd_run(args)
        code = 0
    except SystemExit as e:
        code = e.code
    finally:
        for p in ps:
            p.stop()
    return code, json.loads((tmp_path / "o.json").read_text())


G = lambda m: {"enabled": True, "min_score": m}  # noqa: E731


def test_strict_exit_follows_hard_gate_not_soft(tmp_path):
    # score is ~0.79; soft would fail, hard passes -> exit 0
    code, _ = _run(tmp_path, {"soft_gate": G(0.99), "hard_gate": G(0.1)}, True)
    assert code == 0


def test_strict_exit_1_when_only_hard_fails(tmp_path):
    code, _ = _run(tmp_path, {"soft_gate": G(0.1), "hard_gate": G(0.99)}, True)
    assert code == 1


def test_not_strict_ignores_hard_gate(tmp_path):
    code, _ = _run(tmp_path, {"hard_gate": G(0.99)}, False)
    assert code == 0


def test_snapshot_records_soft_key_not_hard(tmp_path):
    _, snap = _run(tmp_path, {"soft_gate": G(0.99), "hard_gate": G(0.1)}, False)
    assert snap["gate"]["soft"]["min_score"] == 0.99
    assert snap["gate"]["soft"]["passed"] is False


def test_runner_passes_soft_kind(tmp_path):
    sp = tmp_path / "s.yaml"
    sp.write_text(yaml.safe_dump(SPEC))
    ps = _patches(tmp_path)
    with patch("dokimi.runner.run.check_gate", wraps=check_gate) as spy:
        for p in ps:
            p.start()
        try:
            run_benchmark(tmp_path, sp)
        finally:
            for p in ps:
                p.stop()
    assert spy.call_args.args[2] == "soft"
