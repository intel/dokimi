# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""A stop that kept its partial result exits 3 when a budget ran out and 1 when an error did (docs/quickstart.md, exit codes)."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from dokimi import cli
from dokimi.commands.novelty_routing_report import partial_exit_code
from dokimi.novelty import cost as novelty_cost
from dokimi.novelty.client import SpendCap
from tests.cli_paths import novelty_argv
from tests.test_novelty_cap_partial import ROWS, _paid_extractor


@pytest.mark.parametrize("reason, code", [("spend_cap", 3), ("no_price", 3), ("permanent_error", 1),
                                          ("stopped", 1), ("anything else", 1)])
def test_the_exit_code_of_a_kept_partial_result_follows_its_reason(reason, code):
    assert partial_exit_code(reason) == code


@pytest.mark.parametrize("reason, code", [("no_price", 3), ("permanent_error", 1)])
def test_a_probe_stopped_on_a_call_with_no_price_or_a_permanent_error_exits_by_the_reason(
        tmp_path, reason, code):
    from dokimi.commands.novelty_budget import save_probe_run

    with pytest.raises(SystemExit, match="the probes stopped with 1 of 2 answers: because") as stop:
        save_probe_run({"runs": []}, tmp_path / "probes.json", done=1, not_run=1,
                       stop=(reason, "because"))
    assert stop.value.code == code
    assert (tmp_path / "probes.partial.json").exists()


@pytest.mark.parametrize("reason, code", [("no_price", 3), ("permanent_error", 1)])
def test_an_analyze_stopped_on_a_call_with_no_price_or_a_permanent_error_exits_by_the_reason(
        tmp_path, reason, code):
    from dokimi.commands.novelty_budget import save_analyze_partial

    partial = {"reason": reason, "message": "the measurement stopped", "cause": "because"}
    result = SimpleNamespace(partial=partial, artifact={"runs": []})
    with pytest.raises(SystemExit, match="the measurement stopped .because.; the partial result is in") as stop:
        save_analyze_partial(result, tmp_path / "novelty.json")
    assert stop.value.code == code
    assert json.loads((tmp_path / "novelty.partial.json").read_text(encoding="utf-8"))["status"] == "partial"


@pytest.mark.parametrize("reason, code", [("spend_cap", 3), ("permanent_error", 1)])
def test_an_atoms_run_stopped_by_the_faithfulness_check_exits_by_the_reason(
        tmp_path, monkeypatch, reason, code):
    from dokimi.commands.novelty_budget import save_atoms_run
    from dokimi.novelty import atoms as atoms_mod

    monkeypatch.setattr(atoms_mod, "save_atoms", lambda atoms, spans, dest, partial=None, **record:
                        dest.write_text(json.dumps({"partial": partial}), encoding="utf-8"))
    partial = {"reason": reason, "message": "the check stopped"}
    with pytest.raises(SystemExit, match="the check stopped; the partial result is in") as stop:
        save_atoms_run([], [], tmp_path / "atoms.json", partial)
    assert stop.value.code == code
    assert (tmp_path / "atoms.partial.json").exists()


def test_atoms_stopped_by_the_cap_exits_3_through_cli_main_with_its_message_once(
        tmp_path, monkeypatch, capsys):
    artifact = tmp_path / "rows.md"
    artifact.write_text(ROWS, encoding="utf-8")
    cap, calls = SpendCap(limit_usd=1.0), []
    monkeypatch.setattr(novelty_cost, "client", lambda model, provider, role, args:
                        _paid_extractor(cap, calls))
    monkeypatch.setattr("sys.argv", ["dokimi", *novelty_argv("atoms"), "--artifact", str(artifact),
                                     "--model", "e", "--out", str(tmp_path / "atoms.json"),
                                     "--max-usd", "1"])
    with pytest.raises(SystemExit) as stop:
        cli.main()
    assert stop.value.code == 3
    err = capsys.readouterr().err
    assert err.count("--max-usd stopped extraction with 50 of 200 spans read; the partial result is in") == 1
