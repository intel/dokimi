# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Tests for scripts/check_smoke_runs.py — the external-catalog smoke gate."""

from __future__ import annotations

import importlib.util
import io
import json
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check_smoke_runs.py"
_spec = importlib.util.spec_from_file_location("check_smoke_runs", _SCRIPT)
assert _spec and _spec.loader
csr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(csr)


def _runs(oracle=None, probe=None, denied=("example.com:443", "example.com:80")) -> dict:
    clean = {"n": 1, "passed": 1, "errored": 0, "pass_rate": 1.0}
    return {
        "summary": {"per_harness": {
            "docker-oracle": oracle or clean,
            "docker-probe": probe or clean,
        }},
        "results": [
            {"harness": "docker-oracle", "metrics": {}},
            {"harness": "docker-probe", "metrics": {"egress": {"denied_entries": list(denied)}}},
        ],
    }


def _main(tmp_path, data) -> int:
    path = tmp_path / "runs.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return csr.main([str(path)])


def test_a_clean_run_passes(tmp_path):
    assert _main(tmp_path, _runs()) == 0


def test_an_errored_oracle_cell_fails_even_at_pass_rate_one(tmp_path):
    """One pass + one errored cell: the errored one is out of pass_rate's denominator."""
    errored = {"n": 2, "passed": 1, "errored": 1, "pass_rate": 1.0}
    assert _main(tmp_path, _runs(oracle=errored)) == 1
    assert _main(tmp_path, _runs(probe=errored)) == 1


def test_no_cells_or_a_missing_harness_fails():
    assert not csr.smoke_ok(_runs(oracle={"n": 0, "passed": 0, "pass_rate": 0.0}), out=io.StringIO())
    data = _runs()
    del data["summary"]["per_harness"]["docker-probe"]
    assert not csr.smoke_ok(data, out=io.StringIO())


def test_a_probe_the_proxy_never_saw_fails():
    assert not csr.smoke_ok(_runs(denied=()), out=io.StringIO())


def test_a_probe_whose_plain_http_get_was_not_denied_fails():
    """A forward path that skips the allowlist leaves no :80 denial."""
    assert not csr.smoke_ok(_runs(denied=("example.com:443",)), out=io.StringIO())


def test_a_cell_that_failed_without_erroring_fails():
    assert not csr.smoke_ok(_runs(oracle={"n": 2, "passed": 1, "errored": 0, "pass_rate": 0.5}),
                            out=io.StringIO())


def test_an_unreadable_sidecar_log_fails_even_when_every_cell_passed():
    """An empty egress record from a timed-out docker logs is unverified, not clean."""
    data = _runs()
    data["results"].append({"harness": "docker-claude", "metrics": {"egress": {
        "status": "unknown", "denied_entries": []}}})
    assert not csr.smoke_ok(data, out=io.StringIO())


def test_usage_without_a_path():
    assert csr.main([]) == 2
