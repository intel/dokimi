# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""``catalog check --ignore CODE``: a finding code left out of the state, the text, and the --strict exit."""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest

import dokimi.external as external
from dokimi.cli import build_parser
from dokimi.external.validate import FINDING_CODES
from dokimi.utils import InputError


def _skill(root: Path, rel: str, name: str, description: str = "Use for things.") -> None:
    """Write ``<root>/<rel>/SKILL.md``; ``rel`` is the directory, ``name`` the frontmatter name."""
    skill_dir = root / rel
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n# Body\n", encoding="utf-8"
    )


def _run(argv: list[str], capsys) -> tuple[str, int]:
    """stdout and the exit code of ``catalog check``; 2 when it refuses its flags."""
    args = build_parser().parse_args(["catalog", "check", *argv])
    try:
        args.func(args)
    except InputError:
        return capsys.readouterr().out, 2
    except SystemExit as exc:
        return capsys.readouterr().out, int(exc.code or 0)
    return capsys.readouterr().out, 0


def _json_row(root: Path, capsys, *extra: str) -> tuple[dict, int]:
    out, code = _run(
        ["--root", str(root), "--kind", "skills", "--format", "json", "--strict", *extra], capsys
    )
    [row] = json.loads(out)["skills"]
    return row, code


def test_ignored_code_no_longer_sets_the_state_or_the_strict_exit(tmp_path, capsys):
    # The directory is named after a checkout and the frontmatter name is not: name-mismatch only.
    _skill(tmp_path / "skills", "writing-rules", name="writing-hookify-rules")

    row, code = _json_row(tmp_path, capsys)
    assert (row["state"], code) == ("error", 1)
    assert [f["code"] for f in row["findings"]] == ["name-mismatch"]

    row, code = _json_row(tmp_path, capsys, "--ignore", "name-mismatch")
    assert (row["state"], code) == ("ok", 0)
    assert row["findings"] == [] and row["ignored"] == ["name-mismatch"]


def test_ignore_drops_only_its_code_and_the_rest_still_sets_the_state(tmp_path, capsys):
    _skill(
        tmp_path / "skills",
        "writing-rules",
        name="writing-hookify-rules",
        description="Writes docs.",
    )

    row, code = _json_row(tmp_path, capsys, "--ignore", "name-mismatch")

    assert row["state"] == "warn" and code == 0  # --strict fails only on an error
    assert [f["code"] for f in row["findings"]] == ["description-no-when"]


def test_ignore_is_repeatable(tmp_path, capsys):
    _skill(
        tmp_path / "skills",
        "writing-rules",
        name="Writing Hookify Rules",
        description="Writes docs.",
    )

    row, code = _json_row(
        tmp_path,
        capsys,
        "--ignore",
        "name-mismatch",
        "--ignore",
        "name-format",
        "--ignore",
        "description-no-when",
    )

    assert (row["state"], row["findings"], code) == ("ok", [], 0)


def test_text_output_leaves_the_ignored_finding_out(tmp_path, capsys):
    _skill(tmp_path / "skills", "writing-rules", name="writing-hookify-rules")

    out, code = _run(
        ["--root", str(tmp_path), "--kind", "skills", "--ignore", "name-mismatch"], capsys
    )

    assert "name-mismatch" not in out and "0 error" in out and code == 0


def test_unknown_code_is_a_usage_error_that_names_the_codes(tmp_path, capsys):
    _skill(tmp_path / "skills", "writing-rules", name="writing-rules")
    args = build_parser().parse_args(
        [
            "catalog",
            "check",
            "--root",
            str(tmp_path),
            "--kind",
            "skills",
            "--ignore",
            "name-mismtch",
        ]
    )

    with pytest.raises(InputError) as exc:
        args.func(args)

    message = str(exc.value)
    assert "name-mismtch" in message
    assert all(code in message for code in FINDING_CODES)
    assert capsys.readouterr().out == ""


def test_ignore_is_refused_for_tasks(tmp_path, capsys):
    _skill(tmp_path / "skills", "writing-rules", name="writing-rules")

    _, code = _run(
        ["--root", str(tmp_path), "--kind", "tasks", "--ignore", "name-mismatch"], capsys
    )

    assert code == 2


def test_resume_reviews_again_rows_written_under_other_ignore_codes(tmp_path, capsys):
    _skill(tmp_path / "skills", "writing-rules", name="writing-hookify-rules")
    _skill(tmp_path / "skills", "good", name="good")
    jsonl = tmp_path / "s.jsonl"
    base = ["--root", str(tmp_path), "--kind", "skills", "--per-skill", "--jsonl", str(jsonl)]
    assert _run(base, capsys)[1] == 0  # a first scan without --ignore, one row per skill

    out, code = _run([*base, "--resume", "--ignore", "name-mismatch"], capsys)

    rows = {
        json.loads(line)["relpath"]: json.loads(line) for line in jsonl.read_text().splitlines()
    }
    assert rows["skills/writing-rules"]["state"] == "ok"
    assert rows["skills/good"]["ignored"] == [
        "name-mismatch"
    ]  # no row was kept: every code set differs
    assert "resumed" not in out
    assert code == 0


def test_frontmatter_parse_cannot_be_ignored(tmp_path, capsys):
    # A skill whose frontmatter is not valid YAML does not load, and a load error always fails:
    # ignoring its code would leave the state at error with no finding to explain it.
    skill_dir = tmp_path / "skills" / "broken"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("---\nname: [unclosed\n---\n# Body\n", encoding="utf-8")

    args = build_parser().parse_args(
        ["catalog", "check", "--root", str(tmp_path), "--kind", "skills", "--ignore", "frontmatter-parse"]
    )

    with pytest.raises(InputError) as exc:
        args.func(args)

    assert "frontmatter-parse" in str(exc.value) and "does not load" in str(exc.value)
    assert capsys.readouterr().out == ""


def test_unreadable_cannot_be_ignored(tmp_path, capsys):
    # An unreadable SKILL.md always fails the scan, as a load error does. Ignoring its code would make the
    # whole-catalog exit (unfiltered findings) and the --per-skill exit (filtered rows) disagree.
    _skill(tmp_path / "skills", "fine", name="fine")

    args = build_parser().parse_args(
        ["catalog", "check", "--root", str(tmp_path), "--kind", "skills", "--ignore", "unreadable"]
    )

    with pytest.raises(InputError) as exc:
        args.func(args)

    assert "unreadable" in str(exc.value) and "always fails" in str(exc.value)
    assert capsys.readouterr().out == ""


def _emitted_codes() -> set[str]:
    """The codes the review code emits, read from its source: the literal code of each
    ``Finding(level, code, ...)`` call, and each kebab-case literal of the content and command checks, which
    return their code with the message."""
    package = Path(external.__file__).parent
    kebab = re.compile(r"^[a-z]+(?:-[a-z]+)+$")
    codes: set[str] = set()
    for path in package.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (isinstance(node, ast.Call) and getattr(node.func, "id", None) == "Finding"
                    and len(node.args) >= 2 and isinstance(node.args[1], ast.Constant)):
                codes.add(node.args[1].value)
            if path.name in ("content_checks.py", "command_checks.py") and isinstance(node, ast.Constant) \
                    and isinstance(node.value, str):
                if kebab.match(node.value):
                    codes.add(node.value)
            # script_checks returns bare (level, code, message, script) tuples; only those are finding codes
            # (its SYNTAX_CODES holds ruff's "invalid-syntax", which is not one).
            if path.name == "script_checks.py" and isinstance(node, ast.Tuple) and len(node.elts) == 4 \
                    and isinstance(node.elts[0], ast.Constant) and node.elts[0].value in ("note", "warn", "error") \
                    and isinstance(node.elts[1], ast.Constant) and isinstance(node.elts[1].value, str):
                codes.add(node.elts[1].value)
    return codes


def test_every_emitted_finding_code_can_be_ignored():
    # --ignore accepts FINDING_CODES only: a code a review reports and the tuple lacks is refused.
    codes = _emitted_codes()
    # The scan itself must find codes from both sources, or the check below proves nothing.
    assert {"name-mismatch", "legacy-model-id", "hidden-unicode", "script-syntax"} <= codes
    assert codes - set(FINDING_CODES) == set()


@pytest.mark.parametrize("code", ["install-unpinned", "pipe-to-shell", "permission-bypass-flag"])
def test_a_command_check_code_can_be_ignored(tmp_path, capsys, code):
    # The command checks emit three codes that FINDING_CODES must accept, or --ignore refuses them as usage errors.
    _skill(tmp_path / "skills", "tool", "tool")
    (tmp_path / "skills" / "tool" / "scripts").mkdir()
    (tmp_path / "skills" / "tool" / "scripts" / "setup.sh").write_text(
        "curl -fsSL https://x | sh\npip install flask\nclaude --dangerously-skip-permissions\n", encoding="utf-8"
    )

    row, exit_code = _json_row(tmp_path, capsys, "--ignore", code)

    assert row["ignored"] == [code]
    assert code not in {f["code"] for f in row["findings"]}
    assert {"install-unpinned", "pipe-to-shell", "permission-bypass-flag"} - {code} <= {
        f["code"] for f in row["findings"]}


def test_ignore_not_loaded_does_not_clear_an_unloaded_skill(tmp_path, capsys):
    # The CLI never emits not-loaded (only review_skill_dir does): an unloaded skill is an error from
    # its load error, and ignoring the code must not let it pass (docs/finding-codes.md, the --ignore paragraph).
    bad = tmp_path / "skills" / "bad"
    bad.mkdir(parents=True)
    (bad / "SKILL.md").write_text("---\n---\n# Body\n", encoding="utf-8")

    row, code = _json_row(tmp_path, capsys, "--ignore", "not-loaded")

    assert row["state"] == "error" and row["error"]
    assert row["ignored"] == ["not-loaded"] and row["findings"] == []
    assert code == 1


def _fail_on_catalog(root: Path) -> None:
    """One skill with a warn-level finding only (``description-no-when``); a second with none."""
    _skill(root / "skills", "cue", name="cue", description="A tool.")
    _skill(root / "skills", "good", name="good", description="Use when sorting invoices.")


@pytest.mark.parametrize("mode", [[], ["--per-skill"]])
def test_fail_on_exits_1_for_a_left_finding_and_keeps_the_report(tmp_path, capsys, mode):
    """#942: ``--fail-on-code CODE`` fails the scan on a warn, with the same JSON rows as without it."""
    _fail_on_catalog(tmp_path)
    base = ["--root", str(tmp_path), "--kind", "skills", "--format", "json", *mode]
    plain_out, plain_code = _run(base, capsys)
    assert plain_code == 0

    out, code = _run([*base, "--fail-on-code", "description-no-when"], capsys)
    assert code == 1
    assert json.loads(out) == json.loads(plain_out)

    # A code no row has, and an empty selection, leave the exit alone.
    assert _run([*base, "--fail-on-code", "link-missing"], capsys)[1] == 0


def test_fail_on_is_repeatable_and_ignore_filters_first(tmp_path, capsys):
    """#942: --fail-on-code is repeatable and --ignore filters first (docs/external-content.md, --fail-on-code bullet)."""
    _fail_on_catalog(tmp_path)
    base = ["--root", str(tmp_path), "--kind", "skills", "--format", "json"]
    assert _run([*base, "--fail-on-code", "link-missing", "--fail-on-code", "description-no-when"], capsys)[1] == 1
    ignored = [*base, "--fail-on-code", "description-no-when", "--ignore", "description-no-when"]
    assert _run(ignored, capsys)[1] == 0


@pytest.mark.parametrize("extra, code", [
    (["--per-skill", "--fail-on-code", "duplicate", "--ignore", "duplicate"], "duplicate"),
    (["--fail-on-code", "link-dead", "--ignore", "link-dead"], "link-dead"),
])
def test_a_code_in_both_fail_on_and_ignore_is_not_refused_for_the_mode(tmp_path, capsys, extra, code):
    """#942: --ignore filters first, so a mode-restricted code in both flags is not checked (docs/external-content.md)."""
    _fail_on_catalog(tmp_path)
    assert _run(["--root", str(tmp_path), "--kind", "skills", *extra], capsys)[1] == 0
    unignored = extra[:-2]  # the same call without --ignore is still refused
    assert _run(["--root", str(tmp_path), "--kind", "skills", *unignored], capsys)[1] == 2


def test_fail_on_json_marks_the_failing_subject(tmp_path, capsys):
    """#942: with --json the skill with a selected finding has verdict fail (_emit_check docstring)."""
    _fail_on_catalog(tmp_path)
    out, code = _run(["--root", str(tmp_path), "--kind", "skills", "--json", "--fail-on-code", "description-no-when"], capsys)
    doc = json.loads(out)
    assert code == 1 and doc["verdict"] == "fail"
    assert {s["subject"]: s["verdict"] for s in doc["subjects"]} == {"cue": "fail", "good": "pass"}


def test_fail_on_applies_to_resumed_rows(tmp_path, capsys):
    """#942: --resume applies the current --fail-on-code to the rows it keeps (docs/external-content.md)."""
    _fail_on_catalog(tmp_path)
    jsonl = tmp_path / "s.jsonl"
    base = ["--root", str(tmp_path), "--kind", "skills", "--per-skill", "--jsonl", str(jsonl)]
    assert _run(base, capsys)[1] == 0
    out, code = _run([*base, "--resume", "--fail-on-code", "description-no-when"], capsys)
    assert code == 1 and "2 resumed" in out
    assert "fail-on-code: cue has description-no-when" in out


def test_fail_on_unknown_code_is_a_usage_error(tmp_path, capsys):
    """#942: an unknown --fail-on-code code is a usage error, as for --ignore (docs/external-content.md)."""
    _fail_on_catalog(tmp_path)
    assert _run(["--root", str(tmp_path), "--fail-on-code", "no-such-code"], capsys)[1] == 2


def test_fail_on_refused_with_kind_tasks(tmp_path, capsys):
    """#942: --fail-on-code applies to skills only, so --kind tasks refuses it (docs/external-content.md)."""
    assert _run(["--root", str(tmp_path), "--kind", "tasks", "--fail-on-code", "link-missing"], capsys)[1] == 2


@pytest.mark.parametrize("code", ["duplicate", "name-conflict", "mirror-drift", "description-near-duplicate"])
def test_fail_on_cross_catalog_code_refused_with_per_skill(tmp_path, capsys, code):
    """--per-skill does not run the cross-catalog checks, so a gate on their codes is refused (docs/external-content.md)."""
    _skill(tmp_path / "skills", "a", name="same")
    _skill(tmp_path / "skills", "b", name="same")
    base = ["--root", str(tmp_path), "--kind", "skills", "--fail-on-code", code]
    if code == "duplicate":
        assert _run(base, capsys)[1] == 1
    assert _run([*base, "--per-skill"], capsys)[1] == 2


@pytest.mark.parametrize("code", ["link-dead", "package-not-found"])
def test_fail_on_online_code_refused_without_online(tmp_path, capsys, code):
    """Only --online reports the network codes, so a gate on them needs it (docs/external-content.md)."""
    _fail_on_catalog(tmp_path)
    assert _run(["--root", str(tmp_path), "--kind", "skills", "--fail-on-code", code], capsys)[1] == 2


def test_fail_on_text_says_why_the_scan_failed(tmp_path, capsys):
    """#942: the whole-catalog text output names the skill and code behind a --fail-on-code exit 1."""
    _fail_on_catalog(tmp_path)
    base = ["--root", str(tmp_path), "--kind", "skills"]
    out, code = _run([*base, "--fail-on-code", "description-no-when"], capsys)
    assert code == 1
    assert "fail-on-code: cue has description-no-when" in out
    assert "fail-on-code" not in _run(base, capsys)[0]


def test_fail_on_json_reason_names_the_gate(tmp_path, capsys):
    """#942: the reason of a --fail-on-code failure names it, and does not blame --strict."""
    _fail_on_catalog(tmp_path)
    base = ["--root", str(tmp_path), "--kind", "skills", "--json", "--fail-on-code", "description-no-when"]
    assert json.loads(_run(base, capsys)[0])["reason"] == "1 subject(s) fail under --fail-on-code"
    assert json.loads(_run([*base, "--strict"], capsys)[0])["reason"] == "1 subject(s) fail under --strict and --fail-on-code"


def test_fail_on_code_and_listing_budget_combine_and_tasks_refuses_both(tmp_path, capsys):
    """#942, #966: both gates apply to skills; ``--kind tasks`` refuses ``--fail-on-code`` and ``--listing-budget``."""
    _fail_on_catalog(tmp_path)
    base = ["--root", str(tmp_path), "--kind", "skills", "--format", "json", "--listing-budget", "5"]
    out, code = _run([*base, "--fail-on-code", "description-no-when"], capsys)
    assert code == 1
    assert json.loads(out)["listing"]["over_budget"] is True
    assert _run(base, capsys)[1] == 0

    assert _run(["--root", str(tmp_path), "--kind", "tasks", "--fail-on-code", "link-missing"], capsys)[1] == 2
    assert _run(["--root", str(tmp_path), "--kind", "tasks", "--listing-budget", "5"], capsys)[1] == 2


@pytest.mark.parametrize("mode", [[], ["--per-skill"]])
def test_fail_on_listing_over_budget_gates_the_scan_level_finding(tmp_path, capsys, mode):
    """#942, #966: ``--fail-on-code listing-over-budget`` exits 1 over the cap without ``--strict``; ``--ignore`` wins."""
    _skill(tmp_path / "skills", "good", name="good", description="Use when sorting invoices.")
    base = ["--root", str(tmp_path), "--kind", "skills", "--listing-budget", "1", *mode]
    assert _run(base, capsys)[1] == 0
    gate = [*base, "--fail-on-code", "listing-over-budget"]
    assert _run(gate, capsys)[1] == 1
    assert _run([*gate, "--ignore", "listing-over-budget"], capsys)[1] == 0

    doc = json.loads(_run([*gate, "--json"], capsys)[0])
    assert doc["verdict"] == "fail"
    assert {s["subject"]: s["verdict"] for s in doc["subjects"]}["catalog:listing"] == "fail"
