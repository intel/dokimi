# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""``catalog check --online``: how the targets not checked are counted, and when a DNS failure is dead. Mocked HTTP only."""

from __future__ import annotations

import httpx

from dokimi.cli import build_parser
from dokimi.external import online_checks
from tests.test_catalog_validate import _skill
from tests.test_catalog_online import (  # noqa: F401 (the autouse fixture is used here too)
    Web,
    _dns_failure,
    _no_environment_proxy,
    _codes,
    _one_skill,
    _online_codes,
    _run,
    _scan_capture,
)


def test_dns_failure_with_no_http_answer_anywhere_in_the_run_is_not_checked_not_dead(tmp_path):
    url = "https://gone-host.example-tool.dev/"
    root = _one_skill(tmp_path, f"See {url}\n")

    [review], summary = _run(root, Web({url: _dns_failure()}))
    assert _codes(review) == []
    assert summary["checked"] == 0
    assert summary["not_checked"]["dns"] == 1


def test_dns_failure_is_dead_when_another_probe_in_the_run_got_an_answer(tmp_path):
    url = "https://gone-host.example-tool.dev/"
    root = _one_skill(tmp_path, f"See {url} and https://docs.example-tool.dev/ok\n")

    [review], summary = _run(root, Web({url: _dns_failure()}))
    assert _codes(review) == ["link-dead"]
    assert summary["checked"] == 2
    assert summary["not_checked"]["dns"] == 0


def test_a_dns_failure_is_dead_when_a_probe_in_another_skill_got_an_answer(tmp_path):
    url = "https://gone-host.example-tool.dev/"
    _skill(tmp_path / "skills", "a", name="a", description="Use for a.", body=f"See {url}\n")
    _skill(tmp_path / "skills", "b", name="b", description="Use for b.", body="pip install requests==2.31\n")

    reviews, summary = _run(tmp_path, Web({url: _dns_failure()}))
    assert sorted(c for review in reviews for c in _online_codes(review)) == ["link-dead"]
    assert summary["not_checked"]["dns"] == 0


_NOT_CHECKED_BODY = (
    "pip install nosuch-pkg-xyz\n"
    "See https://slow.example-tool.dev/\n"
    + "".join(f"https://docs.example-tool.dev/p{i}\n" for i in range(21))  # the cap keeps 19 of these, drops 2
)


def _not_checked_web() -> Web:
    return Web({"https://slow.example-tool.dev/": httpx.ReadTimeout("timed out")})


def test_online_report_has_an_online_key_with_the_checked_and_not_checked_counts(tmp_path, monkeypatch, capsys):
    root = _one_skill(tmp_path, _NOT_CHECKED_BODY)
    monkeypatch.setattr(online_checks, "make_client", _not_checked_web().client)

    code, report = _scan_capture(root, capsys, "--online")
    assert report["online"] == {"checked": 20, "not_checked": {"network": 1, "dns": 0, "internal": 0, "over_cap": 2}}


def test_without_online_the_report_has_no_online_key(tmp_path, capsys):
    root = _one_skill(tmp_path, _NOT_CHECKED_BODY)

    code, report = _scan_capture(root, capsys)
    assert "online" not in report


def test_online_prints_one_summary_line_with_the_not_checked_counts_and_no_line_per_target(tmp_path, monkeypatch,
                                                                                           capsys):
    root = _one_skill(tmp_path, _NOT_CHECKED_BODY)
    monkeypatch.setattr(online_checks, "make_client", _not_checked_web().client)
    parser = build_parser()
    args = parser.parse_args(["catalog", "check", "--root", str(root), "--kind", "skills", "--online"])
    args.func(args)

    out = capsys.readouterr().out
    assert [line for line in out.splitlines() if "# online:" in line] == [
        "# online: 20 checked, 3 not checked (network error: 1, over the 20-URL cap: 2)"]
    assert "slow.example-tool.dev" not in out  # the timeout is counted, not reported as a warning per target
