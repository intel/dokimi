# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""#960: `catalog repair-receipt` lists the findings one edit cleared, added and left; `--online` gates on `--fail-on-code`."""

from __future__ import annotations

import json

import pytest

from dokimi.cli import build_parser
from dokimi.external import online_checks
from dokimi.external.repair_receipt import repair_receipt
from dokimi.utils import InputError
# `_no_environment_proxy` is an autouse fixture: importing it applies it to every test in this module.
from tests.test_catalog_online import Web, _no_environment_proxy, _one_skill, _scan_capture  # noqa: F401
from tests.test_catalog_validate import _scan_json, _skill


def _row(skill, code, message="m", level="warn"):
    return {"relpath": skill, "findings": [{"level": level, "code": code, "message": message}]}


def _crlf_catalog(root, crlf=("alpha", "beta")):
    skill = _skill(root / "skills", "line-endings", name="line-endings", description="Use when checking line endings.")
    for name in ("alpha", "beta"):
        script = skill / "scripts" / name / "run.sh"
        script.parent.mkdir(parents=True)
        script.write_bytes(b"#!/bin/sh\r\necho hi\r\n" if name in crlf else b"#!/bin/sh\necho hi\n")
    _skill(root / "skills", "fastcontext", name="fastcontext", description="A tool.")
    return root


@pytest.mark.parametrize("fixed", ["alpha", "beta"])
def test_one_script_made_lf_clears_exactly_its_script_crlf_row(tmp_path, capsys, fixed):
    """#960 scenarios 11 and 12: the cleared row is the edited script's, the other script's and the control stay."""
    root = _crlf_catalog(tmp_path)
    before, _ = _scan_json(root, capsys)
    other = "beta" if fixed == "alpha" else "alpha"
    (root / "skills" / "line-endings" / "scripts" / fixed / "run.sh").write_bytes(b"#!/bin/sh\necho hi\n")
    after, _ = _scan_json(root, capsys)

    receipt = repair_receipt(before, after, f"scripts/{fixed}/run.sh")

    assert receipt["edited"] == f"scripts/{fixed}/run.sh"
    assert [r["code"] for r in receipt["cleared"]] == ["script-crlf"]
    assert f"scripts/{fixed}/run.sh" in receipt["cleared"][0]["message"]
    assert receipt["added"] == []
    assert {"description-no-when", "script-crlf"} <= {r["code"] for r in receipt["retained"]}
    assert any(f"scripts/{other}/run.sh" in r["message"] for r in receipt["retained"])
    assert receipt["counts"] == {"cleared": 1, "added": 0, "retained": len(receipt["retained"])}
    assert len(receipt["retained"]) == sum(len(s["findings"]) for s in before["skills"]) - 1


def test_a_finding_the_edit_only_moved_to_another_line_is_retained_and_not_cleared(tmp_path, capsys):
    """#960 scenario 12: deleting one hidden character's line clears that finding only; the one below it moved and stays."""
    root = tmp_path
    body = "# Body\nFirst\u200b one\nplain\nSecond\u200b one\n"
    skill = _skill(root / "skills", "demo", name="demo", description="Use when testing receipts.", body=body)
    before, _ = _scan_json(root, capsys)
    (skill / "SKILL.md").write_text((skill / "SKILL.md").read_text(encoding="utf-8").replace("First\u200b one\n", ""),
                                    encoding="utf-8")
    after, _ = _scan_json(root, capsys)

    receipt = repair_receipt(before, after, "skills/demo/SKILL.md")

    hidden = [f["message"] for f in before["skills"][0]["findings"] if f["code"] == "hidden-unicode"]
    moved = [f["message"] for f in after["skills"][0]["findings"] if f["code"] == "hidden-unicode"]
    assert len(hidden) == 2 and len(moved) == 1 and moved[0] not in hidden
    assert [r["code"] for r in receipt["cleared"]] == ["hidden-unicode"]
    assert receipt["added"] == []
    assert moved[0] in [r["message"] for r in receipt["retained"]]


def test_a_catalog_check_json_envelope_gives_the_same_receipt_as_the_format_json_report(tmp_path, capsys):
    """#960 scenario 11 replays `catalog check --json`: its envelope is read, not refused as a report without `skills`."""
    root = _crlf_catalog(tmp_path)
    report, _ = _scan_json(root, capsys)
    args = build_parser().parse_args(["catalog", "check", "--root", str(root), "--json"])
    args.func(args)
    envelope = json.loads(capsys.readouterr().out)
    assert envelope["schema_version"] == "cli_output.v1" and "skills" not in envelope

    expected = repair_receipt(report, report, "f")
    assert expected["counts"]["retained"] > 0
    assert repair_receipt(envelope, report, "f") == expected
    assert repair_receipt(envelope, envelope, "f") == expected


def test_a_reworded_message_is_one_cleared_row_and_one_added_row():
    """#960 scenario 11: full-row comparison counts a changed wording as one removal and one addition."""
    receipt = repair_receipt({"skills": [_row("s", "x", "old")]}, {"skills": [_row("s", "x", "new")]}, "f")
    assert [r["message"] for r in receipt["cleared"]] == ["old"]
    assert [r["message"] for r in receipt["added"]] == ["new"]
    assert receipt["retained"] == []


def test_the_same_finding_in_two_skills_is_two_rows_and_a_repeat_is_counted_once_per_copy():
    """#960 scenario 11: a row is the skill plus level, code and message (docs/external-content.md, Repair receipt)."""
    twice = {"skills": [_row("a", "x"), _row("b", "x")]}
    assert repair_receipt(twice, {"skills": [_row("b", "x")]}, "f")["cleared"][0]["skill"] == "a"
    dup = {"skills": [{"relpath": "a", "findings": [_row("a", "x")["findings"][0]] * 2}]}
    receipt = repair_receipt(dup, {"skills": [_row("a", "x")]}, "f")
    assert receipt["counts"] == {"cleared": 1, "added": 0, "retained": 1}


def test_a_field_beyond_level_code_and_message_is_not_part_of_the_row():
    """#960 (docs/external-content.md, Repair receipt): a `script` field that differs between scans is the same row."""
    old = {"skills": [{"relpath": "s", "findings": [{"level": "warn", "code": "x", "message": "m", "script": "a.sh"}]}]}
    new = {"skills": [{"relpath": "s", "findings": [{"level": "warn", "code": "x", "message": "m", "script": "b.sh"}]}]}
    receipt = repair_receipt(old, new, "f")
    assert receipt["counts"] == {"cleared": 0, "added": 0, "retained": 1}
    assert receipt["retained"] == [{"skill": "s", "level": "warn", "code": "x", "message": "m"}]


def test_empty_reports_give_an_empty_receipt_and_a_non_report_is_refused():
    """#960: no findings give an empty receipt; a report without `skills` is refused (docs/external-content.md, Repair receipt)."""
    assert repair_receipt({"skills": []}, {"skills": []}, "f")["counts"] == {"cleared": 0, "added": 0, "retained": 0}
    with pytest.raises(ValueError, match="no `skills` list"):
        repair_receipt({}, {"skills": []}, "f")


def _cmd(argv):
    args = build_parser().parse_args(["catalog", "repair-receipt", *argv])
    args.func(args)


def test_the_command_prints_and_saves_the_receipt(tmp_path, capsys):
    before, after = tmp_path / "b.json", tmp_path / "a.json"
    before.write_text(json.dumps({"skills": [_row("s", "x")]}), encoding="utf-8")
    after.write_text(json.dumps({"skills": []}), encoding="utf-8")
    out = tmp_path / "sub" / "receipt.json"

    _cmd([str(before), str(after), "SKILL.md", "--out", str(out)])

    printed = json.loads(capsys.readouterr().out)
    assert printed == json.loads(out.read_text(encoding="utf-8"))
    assert printed["counts"] == {"cleared": 1, "added": 0, "retained": 0}


def test_the_command_refuses_a_missing_file_a_non_report_and_an_unwritable_out(tmp_path):
    good = tmp_path / "good.json"
    good.write_text('{"skills": []}', encoding="utf-8")
    bad = tmp_path / "bad.json"
    bad.write_text("[1]", encoding="utf-8")
    for argv in ([str(tmp_path / "none.json"), str(good)], [str(bad), str(good)],
                 [str(good), str(good), "--out", str(tmp_path)]):
        with pytest.raises(InputError):
            _cmd([*argv[:2], "f", *argv[2:]])
    (tmp_path / "empty.json").write_text("{}", encoding="utf-8")
    with pytest.raises(InputError, match="no `skills` list"):
        _cmd([str(tmp_path / "empty.json"), str(good), "f"])


@pytest.mark.parametrize("body", [
    '{"skills": [null]}',
    '{"skills": [{"relpath": "s", "findings": [null]}]}',
    '{"skills": [{"relpath": "s", "findings": 3}]}',
    '{"skills": [{"relpath": "s", "findings": ["x"]}]}',
    '{"skills": [{"relpath": "s"}]}',
    '{"skills": [{"relpath": "s", "findings": [{}]}]}',
    '{"skills": [{"relpath": "s", "findings": [{"level": "warn", "code": "x"}]}]}',
    '{"skills": [{"relpath": "s", "findings": [{"level": "warn", "code": "x", "message": 3}]}]}',
])
def test_a_malformed_row_is_a_usage_error_and_not_a_traceback(tmp_path, body):
    """#960 (docs/external-content.md, Repair receipt): a file that is not a `catalog check` report is a usage error."""
    bad, good = tmp_path / "bad.json", tmp_path / "good.json"
    bad.write_text(body, encoding="utf-8")
    good.write_text('{"skills": []}', encoding="utf-8")
    with pytest.raises(InputError, match="not a `catalog check --format json` report"):
        _cmd([str(bad), str(good), "f"])
    with pytest.raises(InputError, match="not a `catalog check --format json` report"):
        _cmd([str(good), str(bad), "f"])


@pytest.mark.parametrize("name", ['["s"]', '{"a": 1}', "3"])
def test_a_skill_path_that_is_not_a_string_is_a_usage_error_and_not_a_traceback(tmp_path, name):
    """#960 (docs/external-content.md, Repair receipt): an unhashable `relpath` must not raise a TypeError."""
    bad, good = tmp_path / "bad.json", tmp_path / "good.json"
    bad.write_text('{"skills": [{"relpath": %s, "findings": [{"level": "warn", "code": "x", "message": "m"}]}]}' % name,
                   encoding="utf-8")
    good.write_text('{"skills": []}', encoding="utf-8")
    with pytest.raises(InputError, match="not a `catalog check --format json` report"):
        _cmd([str(bad), str(good), "f"])


@pytest.mark.parametrize("skill", ['{"findings": []}', '{"relpath": false, "findings": []}', '{"relpath": [], "findings": []}',
                                   '{"relpath": 0, "findings": []}', '{"relpath": null, "path": 3, "findings": []}'])
def test_a_missing_or_falsy_invalid_relpath_is_refused_and_not_read_as_a_fallback(tmp_path, skill):
    """#960 (docs/external-content.md, Repair receipt): `relpath` must be present and a string or null before any fallback."""
    bad, good = tmp_path / "bad.json", tmp_path / "good.json"
    bad.write_text('{"skills": [%s]}' % skill, encoding="utf-8")
    good.write_text('{"skills": []}', encoding="utf-8")
    with pytest.raises(InputError, match="not a `catalog check --format json` report"):
        _cmd([str(bad), str(good), "f"])


@pytest.mark.parametrize("findings", ["false", "null", "0", '""', "{}"])
def test_a_falsy_findings_value_is_refused_and_not_read_as_no_findings(tmp_path, findings):
    """#960 (docs/external-content.md, Repair receipt): an invalid `findings` is a usage error, not a cleared row."""
    before, after = tmp_path / "b.json", tmp_path / "a.json"
    before.write_text(json.dumps({"skills": [_row("s", "script-crlf")]}), encoding="utf-8")
    after.write_text('{"skills": [{"relpath": "s", "findings": %s}]}' % findings, encoding="utf-8")
    with pytest.raises(InputError, match="not a `catalog check --format json` report"):
        _cmd([str(before), str(after), "f"])


_ONLINE_BODY = {
    "link-dead": "See [docs](https://acme.dev/gone).\n",
    "package-not-found": "```bash\npip install no-such-pkg-960==1.0\n```\n",
}


@pytest.mark.parametrize("code", ["link-dead", "package-not-found"])
def test_online_fail_on_code_gates_on_the_reported_warning_and_keeps_the_row(tmp_path, monkeypatch, capsys, code):
    """#960 scenario 10: with `--online`, `--fail-on-code` exits 1 for the code found, 0 for the other, and the row is the same."""
    root = _one_skill(tmp_path, _ONLINE_BODY[code])
    web = Web({"https://acme.dev/gone": 404, "https://pypi.org/simple/no-such-pkg-960/": 404})
    monkeypatch.setattr(online_checks, "make_client", web.client)
    other = "package-not-found" if code == "link-dead" else "link-dead"

    plain_code, plain = _scan_capture(root, capsys, "--online")
    gated_code, gated = _scan_capture(root, capsys, "--online", "--fail-on-code", code)
    other_code, other_report = _scan_capture(root, capsys, "--online", "--fail-on-code", other)

    assert [f["code"] for f in plain["skills"][0]["findings"]] == [code]
    assert (plain_code, gated_code, other_code) == (0, 1, 0)
    assert gated["skills"] == plain["skills"] == other_report["skills"]
