# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""#966: ``catalog check --listing-budget`` counts one listing total however the skills are scanned."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dokimi.cli import build_parser
from dokimi.files import InputError

ENTRY = "alpha: Use when alpha runs."  # 27 characters: the name, ": ", the description


def _skill(root: Path, name: str, description: str, extra: str = "") -> None:
    skill_dir = root / "skills" / name
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n{extra}---\n# {name}\n", encoding="utf-8")


def _alpha(root: Path) -> None:
    _skill(root, "alpha", "Use when alpha runs.")


def _run(argv: list[str], capsys) -> tuple[str, int]:
    args = build_parser().parse_args(["catalog", "check", "--kind", "skills", *argv])
    try:
        args.func(args)
    except InputError:
        return capsys.readouterr().out, 2
    except SystemExit as exc:
        return capsys.readouterr().out, int(exc.code or 0)
    return capsys.readouterr().out, 0


def _listing(root: Path, capsys, *argv: str) -> dict:
    out, _ = _run(["--root", str(root), "--format", "json", *argv], capsys)
    return json.loads(out)["listing"]


def test_the_total_is_name_description_and_when_to_use_in_characters(tmp_path, capsys):
    """#966: the count is `name: description` plus ` - when_to_use`, in characters (docs/external-content.md, `--listing-budget`)."""
    _alpha(tmp_path)
    _skill(tmp_path, "cafe", "Use when café opens.", "when_to_use: Mornings.\n")

    total = _listing(tmp_path, capsys, "--listing-budget", "1000")

    assert total == {"chars": len(ENTRY) + len("cafe: Use when café opens. - Mornings."), "budget": 1000,
                     "over_budget": False}
    assert len(ENTRY) == 27


@pytest.mark.parametrize("scan", [[], ["--per-skill"], ["--per-skill", "--concurrency", "2"]])
def test_every_scan_path_reports_the_same_total(tmp_path, capsys, scan):
    """#966: the whole-catalog scan, `--per-skill` and `--concurrency` give one total."""
    _alpha(tmp_path)
    _skill(tmp_path, "bravo", "Use when bravo runs.")

    assert _listing(tmp_path, capsys, *scan)["chars"] == 27 + len("bravo: Use when bravo runs.")


def test_a_skill_the_model_is_not_offered_is_not_counted(tmp_path, capsys):
    """#966: a `disable-model-invocation` skill is not in the routing field (docs/catalog-jsonl.md, `listing_chars`)."""
    _alpha(tmp_path)
    _skill(tmp_path, "hidden", "Use when hidden runs.", "disable-model-invocation: true\n")

    assert _listing(tmp_path, capsys)["chars"] == 27


@pytest.mark.parametrize("cap, over", [(28, False), (27, False), (26, True)])
def test_the_cap_is_inclusive_and_over_it_warns_and_fails_only_under_strict(tmp_path, capsys, cap, over):
    """#966: a total equal to the cap passes; over it is a `listing-over-budget` warning and exit 1 only under `--strict` (docs/finding-codes.md)."""
    _alpha(tmp_path)
    argv = ["--root", str(tmp_path), "--listing-budget", str(cap)]

    out, code = _run(argv, capsys)
    strict_out, strict_code = _run([*argv, "--strict"], capsys)

    assert ("listing-over-budget" in out) is over
    assert ("27 characters" in out) is over
    assert code == 0
    assert strict_code == (1 if over else 0)
    assert "listing-over-budget" in strict_out or not over


@pytest.mark.parametrize("cap, over", [(28, False), (27, False), (26, True)])
def test_resume_applies_this_runs_cap_to_a_kept_row(tmp_path, capsys, cap, over):
    """#966 scenario 3: a resumed row gets the same budget verdict as a fresh scan at this run's cap."""
    _alpha(tmp_path)
    receipt = tmp_path / "receipt.jsonl"
    base = ["--root", str(tmp_path), "--per-skill", "--jsonl", str(receipt)]
    assert _run(base, capsys)[1] == 0

    out, code = _run([*base, "--resume", "--listing-budget", str(cap), "--strict"], capsys)
    fresh, fresh_code = _run([*base, "--listing-budget", str(cap), "--strict"], capsys)

    assert "1 resumed" in out
    assert code == fresh_code == (1 if over else 0)
    assert ("listing-over-budget" in out) is ("listing-over-budget" in fresh) is over


def test_ignoring_the_code_drops_the_warning_and_the_exit_but_not_the_total(tmp_path, capsys):
    """#966 scenario 4: `--ignore listing-over-budget` drops the warning and exit; the report keeps the total."""
    _alpha(tmp_path)
    argv = ["--root", str(tmp_path), "--per-skill", "--strict", "--listing-budget", "26",
            "--ignore", "listing-over-budget"]

    out, code = _run(argv, capsys)
    listing = _listing(tmp_path, capsys, *argv[2:])

    assert (code, "listing-over-budget" in out) == (0, False)
    assert listing == {"chars": 27, "budget": 26, "over_budget": True}


def test_resume_reviews_again_a_row_that_has_no_listing_count(tmp_path, capsys):
    """#966: `--resume` redoes a row without `listing_chars` (docs/catalog-jsonl.md)."""
    _alpha(tmp_path)
    receipt = tmp_path / "receipt.jsonl"
    base = ["--root", str(tmp_path), "--per-skill", "--jsonl", str(receipt)]
    assert _run(base, capsys)[1] == 0
    row = json.loads(receipt.read_text(encoding="utf-8"))
    del row["listing_chars"]
    receipt.write_text(json.dumps(row) + "\n", encoding="utf-8")

    out, _ = _run([*base, "--resume", "--listing-budget", "26"], capsys)

    assert "resumed" not in out
    assert json.loads(receipt.read_text(encoding="utf-8"))["listing_chars"] == 27


def test_an_empty_catalog_has_no_listing_to_budget(tmp_path, capsys):
    """#966: no skills total 0 characters and are never over the cap (docs/external-content.md, `--listing-budget`)."""
    (tmp_path / "skills").mkdir()

    out, code = _run(["--root", str(tmp_path), "--format", "json", "--listing-budget", "1", "--strict"], capsys)

    assert json.loads(out)["listing"] == {"chars": 0, "budget": 1, "over_budget": False}
    assert code == 1  # no SKILL.md found: the scan's own failure, not the budget's


def test_the_budget_is_refused_for_a_tasks_scan(tmp_path, capsys):
    """#966: `--listing-budget` applies to skills, so `--kind tasks` exits 2 (docs/external-content.md)."""
    out, code = _run(["--root", str(tmp_path), "--kind", "tasks", "--listing-budget", "5"], capsys)

    assert code == 2


@pytest.mark.parametrize("scan", [[], ["--per-skill"], ["--per-skill", "--concurrency", "2"]])
def test_a_mirror_is_counted_once_as_the_routing_field_lists_it(tmp_path, capsys, scan):
    """#966: a byte-identical SKILL.md under another path is one card in the field (`routing.catalog_competitors`), so one entry in the total."""
    _alpha(tmp_path)
    mirror = tmp_path / "plugins" / "x" / "skills" / "alpha"
    mirror.mkdir(parents=True)
    (mirror / "SKILL.md").write_bytes((tmp_path / "skills" / "alpha" / "SKILL.md").read_bytes())
    argv = ["--root", str(tmp_path), "--strict", "--listing-budget", "27", *scan]

    out, code = _run([*argv, "--format", "json"], capsys)

    assert json.loads(out)["listing"] == {"chars": 27, "budget": 27, "over_budget": False}
    assert code == 0


def test_a_name_with_differing_copies_is_left_out_of_the_total(tmp_path, capsys):
    """#966: copies of one name that differ in content leave the field (`routing.catalog_competitors`, `_conflicting_names`), so the total has none of them."""
    from dokimi.novelty.routing import catalog_competitors

    _alpha(tmp_path)
    _skill(tmp_path, "bravo", "Use when bravo runs.")
    other = tmp_path / "plugins" / "x" / "skills" / "alpha"
    other.mkdir(parents=True)
    (other / "SKILL.md").write_text(
        "---\nname: alpha\ndescription: Use when alpha runs twice.\n---\n# alpha\n", encoding="utf-8")
    assert [c.name for c in catalog_competitors(str(tmp_path), set())] == ["bravo"]

    for scan in ([], ["--per-skill"]):
        assert _listing(tmp_path, capsys, *scan)["chars"] == len("bravo: Use when bravo runs.")


def test_json_names_the_budget_as_the_reason_a_scan_failed(tmp_path, capsys):
    """#966: `catalog check --json` under `--strict` says the listing is over the cap, not 'nothing to scan'."""
    _alpha(tmp_path)

    out, code = _run(["--root", str(tmp_path), "--listing-budget", "26", "--strict", "--json"], capsys)

    doc = json.loads(out)
    assert code == 1
    assert doc["reason"] != "nothing to scan"
    failing = [s for s in doc["subjects"] if s["verdict"] == "fail"]
    assert [f["code"] for s in failing for f in s["findings"]] == ["listing-over-budget"]


def test_json_reports_the_budget_finding_without_strict(tmp_path, capsys):
    """#966: `--strict` decides the exit, not whether `catalog check --json` carries the `listing-over-budget` warning the text output prints."""
    _alpha(tmp_path)

    out, code = _run(["--root", str(tmp_path), "--listing-budget", "26", "--json"], capsys)

    doc = json.loads(out)
    assert code == 0
    listing = [s for s in doc["subjects"] if s["subject"] == "catalog:listing"]
    assert [f["code"] for s in listing for f in s["findings"]] == ["listing-over-budget"]
    assert [s["verdict"] for s in listing] == ["pass"]


def _manifest(root: Path, entries: list[tuple[str, str]]) -> None:
    lines = "".join(f'  - name: "{n}"\n    status: "incubating"\n    catalog_path: "{p}"\n' for n, p in entries)
    (root / "skills.yaml").write_text(f'schema_version: "1"\nskills:\n{lines}', encoding="utf-8")


@pytest.mark.parametrize("scan", [[], ["--per-skill"]])
def test_a_mirror_under_another_manifest_name_is_counted_once(tmp_path, capsys, scan):
    """#966: copies group by the loaded skill name, not the manifest alias: `primary` and `mirror` of `alpha` are 27, not 54."""
    _alpha(tmp_path)
    mirror = tmp_path / "plugins" / "x" / "skills" / "alpha"
    mirror.mkdir(parents=True)
    (mirror / "SKILL.md").write_bytes((tmp_path / "skills" / "alpha" / "SKILL.md").read_bytes())
    _manifest(tmp_path, [("primary", "skills/alpha"), ("mirror", "plugins/x/skills/alpha")])

    assert _listing(tmp_path, capsys, *scan)["chars"] == 27


@pytest.mark.parametrize("scan", [[], ["--per-skill"]])
def test_one_manifest_name_on_two_loaded_skills_is_not_a_conflict(tmp_path, capsys, scan):
    """#966: two different skills listed under one manifest name are both in the field, so both count."""
    _alpha(tmp_path)
    _skill(tmp_path, "bravo", "Use when bravo runs.")
    _manifest(tmp_path, [("same", "skills/alpha"), ("same", "skills/bravo")])

    assert _listing(tmp_path, capsys, *scan)["chars"] == 27 + len("bravo: Use when bravo runs.")


def test_resume_reviews_again_a_row_that_has_no_listing_name(tmp_path, capsys):
    """#966: a receipt row without `listing_name` cannot be grouped, so `--resume` redoes it."""
    _alpha(tmp_path)
    receipt = tmp_path / "receipt.jsonl"
    base = ["--root", str(tmp_path), "--per-skill", "--jsonl", str(receipt)]
    assert _run(base, capsys)[1] == 0
    row = json.loads(receipt.read_text(encoding="utf-8"))
    del row["listing_name"]
    receipt.write_text(json.dumps(row) + "\n", encoding="utf-8")

    out, _ = _run([*base, "--resume"], capsys)

    assert "resumed" not in out
    assert json.loads(receipt.read_text(encoding="utf-8"))["listing_name"] == "alpha"


@pytest.mark.parametrize("bad", ['"27"', "-27", "true", "2.5"])
def test_resume_refuses_a_listing_count_that_is_not_a_nonnegative_integer(tmp_path, capsys, bad):
    """#966: `listing_chars` of "27" would raise in the sum and -27 would lower the total; the row is refused."""
    _alpha(tmp_path)
    receipt = tmp_path / "receipt.jsonl"
    base = ["--root", str(tmp_path), "--per-skill", "--jsonl", str(receipt)]
    assert _run(base, capsys)[1] == 0
    row = json.loads(receipt.read_text(encoding="utf-8"))
    receipt.write_text(json.dumps(row).replace('"listing_chars": 27', f'"listing_chars": {bad}') + "\n", encoding="utf-8")

    out, code = _run([*base, "--resume", "--listing-budget", "26"], capsys)

    assert code == 2


def test_a_description_over_the_harness_cut_counts_at_the_cut(tmp_path, capsys):
    """#966: Claude Code cuts `description - when_to_use` at 1,536 characters (`skill_treatment.LISTING_MAX_DESC_CHARS`), so `a` counts 1 + 2 + 1536."""
    from dokimi.harnesses.skill_treatment import LISTING_MAX_DESC_CHARS

    _skill(tmp_path, "a", "x" * 3000)
    _skill(tmp_path, "b", "short")

    for scan in ([], ["--per-skill"]):
        total = _listing(tmp_path, capsys, "--listing-budget", "2000", *scan)
        assert total["chars"] == len("a: ") + LISTING_MAX_DESC_CHARS + len("b: short")
        assert total["over_budget"] is False


def test_an_empty_when_to_use_adds_no_separator(tmp_path, capsys):
    """#966: `when_to_use: ""` is no text, so `b: short` is 8 characters, not 11 (`repos/budget.py` counts it as 0)."""
    _skill(tmp_path, "b", "short", 'when_to_use: ""\n')

    assert _listing(tmp_path, capsys)["chars"] == len("b: short")


def test_the_listing_total_comes_before_the_granularity_section(tmp_path, capsys):
    """#966: docs/finding-codes.md puts the `listing-over-budget` line after the counts, before `# granularity`."""
    _alpha(tmp_path)

    out, _ = _run(["--root", str(tmp_path), "--listing-budget", "26"], capsys)

    assert out.index("warn listing-over-budget") < out.index("# granularity")


def test_a_skill_named_listing_does_not_share_the_total_subject(tmp_path, capsys):
    """#966: a skill can be named `listing`, so the total's subject is `catalog:listing`, which no skill name can be."""
    _skill(tmp_path, "listing", "Use when listing runs.")

    out, _ = _run(["--root", str(tmp_path), "--listing-budget", "5", "--json"], capsys)

    names = [s["subject"] for s in json.loads(out)["subjects"]]
    assert sorted(names) == ["catalog:listing", "listing"]


def test_ignoring_the_listing_code_does_not_redo_resumed_rows(tmp_path, capsys):
    """#966: `listing-over-budget` is the scan's code, in no row, so `--resume --ignore listing-over-budget` keeps every row."""
    _alpha(tmp_path)
    receipt = tmp_path / "receipt.jsonl"
    base = ["--root", str(tmp_path), "--per-skill", "--jsonl", str(receipt)]
    assert _run(base, capsys)[1] == 0

    out, _ = _run([*base, "--resume", "--ignore", "listing-over-budget"], capsys)

    assert "1 resumed" in out
    assert json.loads(receipt.read_text(encoding="utf-8"))["ignored"] == []


@pytest.mark.parametrize("bad", ["[]", "{}", "5"])
def test_resume_refuses_a_content_digest_that_is_not_a_string(tmp_path, capsys, bad):
    """#966: the total keys a set on `content_sha256`; a list would raise TypeError, so the row is refused with the input error."""
    _alpha(tmp_path)
    receipt = tmp_path / "receipt.jsonl"
    base = ["--root", str(tmp_path), "--per-skill", "--jsonl", str(receipt)]
    assert _run(base, capsys)[1] == 0
    row = json.loads(receipt.read_text(encoding="utf-8"))
    row["content_sha256"] = json.loads(bad)
    receipt.write_text(json.dumps(row) + "\n", encoding="utf-8")

    _, code = _run([*base, "--resume"], capsys)

    assert code == 2


def test_the_tally_keeps_no_row_for_the_listing_total():
    """#966: a streamed per-skill row is freed once written; the tally holds counts, not the rows and their findings."""
    from dokimi.commands.catalog_scan import _Tally

    tally = _Tally(False, Path("."))
    tally.add({"state": "ok", "error": None, "findings": [], "listing_chars": 27, "listing_name": "a",
               "content_sha256": "d", "name": "a", "relpath": None})

    assert tally.listing.total() == 27
    assert not any(isinstance(v, list) and v and isinstance(v[0], dict) for v in vars(tally).values())


@pytest.mark.parametrize("finding", [{"level": "warn", "message": "m"}, {"code": 5}, "x"])
def test_resume_refuses_a_finding_without_a_string_code(tmp_path, capsys, finding):
    """A kept row whose finding has no string `code` is refused with the input error, not a KeyError from the rate tally."""
    _alpha(tmp_path)
    receipt = tmp_path / "receipt.jsonl"
    base = ["--root", str(tmp_path), "--per-skill", "--jsonl", str(receipt)]
    assert _run(base, capsys)[1] == 0
    row = json.loads(receipt.read_text(encoding="utf-8"))
    row["findings"] = [finding]
    receipt.write_text(json.dumps(row) + "\n", encoding="utf-8")

    _, code = _run([*base, "--resume"], capsys)

    assert code == 2
