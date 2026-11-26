# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""``catalog check --online --fail-on-code``: the gate on an online finding. Mocked HTTP only."""

from __future__ import annotations

import json

import pytest

from dokimi.cli import build_parser
from dokimi.external import online_checks
from dokimi.utils import InputError
from tests.test_catalog_online import PYPI, Web
from tests.test_catalog_validate import _skill


def _scan(root, capsys, *extra: str) -> tuple[int, dict]:
    args = build_parser().parse_args(["catalog", "check", "--root", str(root), "--kind", "skills",
                                      "--format", "json", *extra])
    code = 0
    try:
        args.func(args)
    except SystemExit as exc:
        code = exc.code
    return code, json.loads(capsys.readouterr().out)


@pytest.mark.parametrize("code, body, answers", [
    ("link-dead", "See [the guide](https://docs.acme.dev/gone).\n", {"https://docs.acme.dev/gone": 404}),
    ("package-not-found", "pip install nosuch-pkg-xyz\n", {PYPI.format(name="nosuch-pkg-xyz"): 404}),
])
def test_fail_on_code_gates_an_online_finding_and_keeps_the_row(tmp_path, monkeypatch, capsys, code, body, answers):
    """#960 (scenario 10): ``--online --fail-on-code CODE`` exits 1 on that online finding, with the report unchanged."""
    _skill(tmp_path / "skills", "s", name="s", description="Use when checking.", body=body)
    monkeypatch.setattr(online_checks, "make_client", Web(answers).client)

    plain_code, plain = _scan(tmp_path, capsys, "--online")
    assert plain_code == 0 and [f["code"] for f in plain["skills"][0]["findings"]] == [code]

    gated_code, gated = _scan(tmp_path, capsys, "--online", "--fail-on-code", code)
    assert gated_code == 1
    assert gated["skills"] == plain["skills"]

    other = "package-not-found" if code == "link-dead" else "link-dead"
    assert _scan(tmp_path, capsys, "--online", "--fail-on-code", other)[0] == 0


def test_fail_on_code_for_an_online_code_without_online_is_a_usage_error(tmp_path):
    """#960 (scenario 10): an online code is refused without ``--online`` (``_refuse_unchecked_fail_on``)."""
    args = build_parser().parse_args(["catalog", "check", "--root", str(tmp_path), "--kind", "skills",
                                      "--fail-on-code", "link-dead"])
    with pytest.raises(InputError, match="--online"):
        args.func(args)
