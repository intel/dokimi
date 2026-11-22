# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Per-skill and per-repo shares in ``catalog check`` (roadmap item 19): each repo counts once in the repo share."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from dokimi.external.catalog_rates import RateTally, text_lines
from tests.test_catalog_ignore import _run, _skill

WHEN = "Use when testing things."
NO_WHEN = "Handles testing things."  # no when-cue: a description-no-when warning


def _row(relpath: str, state: str, *codes: str) -> dict:
    return {"relpath": relpath, "state": state, "findings": [{"code": code} for code in codes]}


def _repo(root: Path, rel: str) -> None:
    (root / rel / ".git").mkdir(parents=True)


def test_each_repo_counts_once_in_the_repo_share(tmp_path):
    _repo(tmp_path, "a")
    _repo(tmp_path, "b")
    tally = RateTally(tmp_path)
    for row in (_row("a/s1", "warn", "link-missing"), _row("a/s2", "ok"), _row("a/s3", "ok"),
                _row("b/t1", "warn", "link-missing")):
        tally.add(row)

    summary = tally.summary()
    assert summary["repos"] == 2 and summary["skills"] == 4
    # Per skill: 2 of 4 are warn. Per repo: a has 1 of 3 warn, b has 1 of 1; the mean of those is 2/3.
    assert summary["states"]["warn"] == {"skills": 2, "rate": 0.5, "repo_rate": 0.6667}
    assert summary["states"]["ok"] == {"skills": 2, "rate": 0.5, "repo_rate": 0.3333}
    assert summary["states"]["error"] == {"skills": 0, "rate": 0.0, "repo_rate": 0.0}
    assert summary["codes"] == {"link-missing": {"skills": 2, "rate": 0.5, "repo_rate": 0.6667}}


def test_the_nearest_repo_holds_a_nested_skill(tmp_path):
    _repo(tmp_path, "vendor/x")
    tally = RateTally(tmp_path)
    tally.add(_row("vendor/x/skills/s", "ok"))
    tally.add(_row("loose/s", "ok"))  # no .git above it: the scanned root is its repo
    assert set(tally.skills) == {"vendor/x", "."}


def test_a_skill_in_a_repo_nested_in_another_counts_once_per_code_in_the_inner_repo(tmp_path):
    # mono holds vendor/x, its own repo: b belongs to the nearer one, and its two link-missing count once.
    _repo(tmp_path, "mono")
    _repo(tmp_path, "mono/vendor/x")
    _repo(tmp_path, "other")
    tally = RateTally(tmp_path)
    for row in (_row("mono/a", "ok"), _row("mono/vendor/x/b", "warn", "link-missing", "link-missing"),
                _row("other/c", "ok")):
        tally.add(row)

    summary = tally.summary()
    assert summary["repos"] == 3
    assert summary["codes"]["link-missing"] == {"skills": 1, "rate": 0.3333, "repo_rate": 0.3333}


def test_a_skill_whose_own_directory_is_a_repo_is_its_repo(tmp_path):
    _repo(tmp_path, "solo")
    tally = RateTally(tmp_path)
    tally.add(_row("solo", "ok"))
    assert set(tally.skills) == {"solo"}


@pytest.mark.parametrize("repos", [1, 2])
def test_the_text_names_the_shares_only_with_several_repos(repos):
    summary = {"repos": repos, "skills": 2, "states": {
        "ok": {"skills": 1, "rate": 0.5, "repo_rate": 0.5},
        "warn": {"skills": 1, "rate": 0.5, "repo_rate": 0.5},
        "error": {"skills": 0, "rate": 0.0, "repo_rate": 0.0},
        "skipped": {"skills": 0, "rate": 0.0, "repo_rate": 0.0},
        "not_checked": {"skills": 0, "rate": 0.0, "repo_rate": 0.0},
    }, "codes": {}}
    out = text_lines(summary)
    if repos == 1:
        assert out == []
    else:
        assert out[0] == "# rates per skill and per repo (2 repos, each repo counted once):"
        assert len(out) == 6  # the header and the five states


def test_whole_catalog_json_gives_the_shares_per_skill_and_per_repo(tmp_path, capsys):
    _repo(tmp_path, "big")
    _repo(tmp_path, "small")
    _skill(tmp_path / "big", "s1", name="s1", description=WHEN)
    _skill(tmp_path / "big", "s2", name="s2", description=NO_WHEN)
    _skill(tmp_path / "big", "s3", name="s3", description=NO_WHEN)
    _skill(tmp_path / "small", "t1", name="t1", description=WHEN)

    out, _ = _run(["--root", str(tmp_path), "--kind", "skills", "--format", "json"], capsys)
    rates = json.loads(out)["rates"]

    assert rates["repos"] == 2 and rates["skills"] == 4
    warn = rates["states"]["warn"]
    assert warn["skills"] == 2 and warn["rate"] == 0.5
    # big has 2 of 3 warn, small none: the repo share is (2/3 + 0) / 2.
    assert warn["repo_rate"] == 0.3333
    assert rates["states"]["ok"]["repo_rate"] == 0.6667


def test_the_per_skill_path_reports_the_same_shares(tmp_path, capsys):
    _repo(tmp_path, "big")
    _repo(tmp_path, "small")
    _skill(tmp_path / "big", "s1", name="s1", description=WHEN)
    _skill(tmp_path / "big", "s2", name="s2", description=NO_WHEN)
    _skill(tmp_path / "big", "s3", name="s3", description=NO_WHEN)
    _skill(tmp_path / "small", "t1", name="t1", description=WHEN)

    out, _ = _run(["--root", str(tmp_path), "--kind", "skills", "--per-skill", "--format", "json"], capsys)
    report = json.loads(out)

    assert report["rates"]["states"]["warn"] == {"skills": 2, "rate": 0.5, "repo_rate": 0.3333}


@pytest.mark.parametrize("scan_args", [[], ["--per-skill"]])
def test_the_text_output_names_the_repo_shares(tmp_path, capsys, scan_args):
    _repo(tmp_path, "big")
    _repo(tmp_path, "small")
    _skill(tmp_path / "big", "s1", name="s1", description=WHEN)
    _skill(tmp_path / "small", "t1", name="t1", description=NO_WHEN)

    out, _ = _run(["--root", str(tmp_path), "--kind", "skills", *scan_args], capsys)
    assert "# rates per skill and per repo (2 repos, each repo counted once):" in out
    # Per skill 1 of 2 is warn; per repo big has none and small all, so the repo share is also 50%.
    assert "#   warn: 50.0% of skills, 50.0% per repo (1 skills)" in out


def test_a_dotdot_in_a_relpath_names_the_same_repo(tmp_path):
    _repo(tmp_path, "repo")
    (tmp_path / "repo" / "extra").mkdir()
    tally = RateTally(tmp_path)
    for row in (_row("repo/a", "warn"), _row("repo/extra/../b", "ok"), _row("repo/c", "ok")):
        tally.add(row)

    summary = tally.summary()
    assert summary["repos"] == 1
    assert summary["states"]["warn"] == {"skills": 1, "rate": 0.3333, "repo_rate": 0.3333}


def test_an_absolute_relpath_under_a_relative_root_names_its_repo(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _repo(tmp_path, "catalog/a")
    _repo(tmp_path, "catalog/b")
    tally = RateTally(Path("catalog"))
    for rel in (str(tmp_path / "catalog/a/s1"), str(tmp_path / "catalog/a/s2"), "b/t1"):
        tally.add(_row(rel, "ok"))

    assert sorted(tally.skills) == ["a", "b"]



def test_unchecked_and_skipped_rows_have_their_own_rates(tmp_path):
    tally = RateTally(tmp_path)
    tally.add(_row('a', 'ok', 'description-when-not-checked'))
    tally.add(_row('b', 'ok'))
    tally.add(_row('c', 'skipped'))
    states = tally.summary()['states']
    for state in ('ok', 'not_checked', 'skipped'):
        assert states[state] == {'skills': 1, 'rate': 0.3333, 'repo_rate': 0.3333}


def test_a_warn_row_with_a_not_checked_note_counts_under_warn(tmp_path):
    tally = RateTally(tmp_path)
    tally.add(_row("a", "warn", "description-when-not-checked"))
    states = tally.summary()["states"]
    assert states["warn"]["skills"] == 1
    assert states["not_checked"]["skills"] == 0


def test_an_ok_row_with_a_finding_that_has_no_code_counts_under_ok(tmp_path):
    tally = RateTally(tmp_path)
    tally.add({"relpath": "s", "state": "ok", "findings": [{"level": "warn", "message": "m"}]})
    assert tally.summary()["states"]["ok"]["skills"] == 1


def test_empty_catalog_rates_have_no_denominator(tmp_path):
    states = RateTally(tmp_path).summary()["states"]
    assert all(entry == {"skills": 0, "rate": None, "repo_rate": None} for entry in states.values())


def test_two_names_for_one_repo_directory_share_its_bucket(tmp_path):
    _repo(tmp_path, "Repo")
    alias = tmp_path / "repo"
    if not alias.is_dir():  # on a case-sensitive filesystem, make a second name for the directory
        alias.symlink_to(tmp_path / "Repo", target_is_directory=True)
    tally = RateTally(tmp_path)
    for path, state in [("Repo/a", "warn"), ("repo/b", "ok"), ("Repo/c", "ok")]:
        tally.add(_row(path, state))
    report = tally.summary()
    assert report["repos"] == 1
    assert report["states"]["warn"]["repo_rate"] == 0.3333


def test_text_includes_a_code_common_across_small_repos(tmp_path):
    _repo(tmp_path, "big")
    tally = RateTally(tmp_path)
    big = [f"big-{n}" for n in range(5)]
    for i in range(10):
        codes = big if i < 6 else []
        tally.add(_row(f"big/s{i}", "warn" if codes else "ok", *codes))
    for repo in ("small-a", "small-b", "small-c"):
        _repo(tmp_path, repo)
        tally.add(_row(f"{repo}/s", "warn", "widespread", *(big if repo == "small-a" else [])))
    lines = text_lines(tally.summary())
    assert any(line.startswith("#   widespread:") for line in lines)
    assert len(lines) == 11  # the header, the five states, and the five codes


def test_the_text_ranks_a_code_by_its_larger_share(tmp_path):
    _repo(tmp_path, "big")
    tally = RateTally(tmp_path)
    for i in range(30):
        tally.add(_row(f"big/s{i}", "warn", "p", *(["w1", "w2", "w3", "w4", "w5"] if i < 10 else [])))
    for repo in ("w-a", "w-b", "w-c", "w-d"):
        _repo(tmp_path, repo)
        tally.add(_row(f"{repo}/s", "warn", "w1", "w2", "w3", "w4", "w5"))
    assert any(line.startswith("#   p:") for line in text_lines(tally.summary()))


def test_filesystem_without_inode_numbers_keeps_different_repos(tmp_path, monkeypatch):
    import os

    for repo in ("a", "b"):
        _repo(tmp_path, repo)
    real_stat = Path.stat

    def zero_inode(path, **kwargs):
        fields = list(real_stat(path, **kwargs))
        fields[1] = 0  # os.stat_result's st_ino field
        return os.stat_result(fields)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "stat", zero_inode)
        tally = RateTally(tmp_path)
        tally.add(_row("a/s", "ok"))
        tally.add(_row("b/t", "warn"))
        assert tally.summary()["repos"] == 2


def test_symlink_then_parent_follows_the_target_repository(tmp_path):
    _repo(tmp_path, "a")
    _repo(tmp_path, "b")
    (tmp_path / "b/sub").mkdir()
    (tmp_path / "a/link").symlink_to(tmp_path / "b/sub", target_is_directory=True)
    tally = RateTally(tmp_path)
    for path, state in [("a/s1", "warn"), ("a/link/../s2", "ok"), ("b/s3", "ok")]:
        tally.add(_row(path, state))
    assert tally.summary()["states"]["warn"]["repo_rate"] == 0.5


def test_alias_of_root_shares_its_fallback_bucket(tmp_path):
    (tmp_path / ".git").mkdir()
    (tmp_path / "alias").symlink_to(tmp_path, target_is_directory=True)
    tally = RateTally(tmp_path)
    for path, state in [("s1", "warn"), ("s2", "ok"), ("alias/s3", "ok")]:
        tally.add(_row(path, state))
    assert tally.summary()["repos"] == 1
    assert tally.summary()["states"]["warn"]["repo_rate"] == 0.3333
    assert text_lines(tally.summary()) == []


def test_an_unreadable_skill_directory_is_counted_not_raised(tmp_path, monkeypatch):
    import errno

    (tmp_path / "locked").mkdir()
    real_stat = Path.stat

    def denied(path, **kwargs):
        if path.name == ".git" and path.parent.name == "locked":
            raise PermissionError(errno.EACCES, "Permission denied", str(path))
        return real_stat(path, **kwargs)

    monkeypatch.setattr(Path, "stat", denied)
    tally = RateTally(tmp_path)
    tally.add(_row("locked", "error", "load-error"))
    assert tally.summary()["skills"] == 1


def test_two_spellings_of_one_inode_are_one_repo(tmp_path, monkeypatch):
    import os

    _repo(tmp_path, "Repo/a")
    _repo(tmp_path, "repo/b")
    real_stat = Path.stat

    def shared_inode(path, **kwargs):
        fields = list(real_stat(path, **kwargs))
        if path.name in ("a", "b"):
            fields[1] = 7
        return os.stat_result(fields)

    monkeypatch.setattr(Path, "stat", shared_inode)
    tally = RateTally(tmp_path)
    tally.add(_row("Repo/a/s", "ok"))
    tally.add(_row("repo/b/t", "warn"))
    assert tally.summary()["repos"] == 1
    assert tally.summary()["states"]["warn"]["repo_rate"] == 0.5
    assert text_lines(tally.summary()) == []


def test_symlinked_skill_md_counts_in_its_own_directory_repo(tmp_path):
    for repo in ("a", "b"):
        (tmp_path / repo / ".git").mkdir(parents=True)
    (tmp_path / "b" / "s").mkdir()
    (tmp_path / "b" / "s" / "SKILL.md").write_text("---\nname: s\n---\n", encoding="utf-8")
    (tmp_path / "a" / "s").mkdir()
    os.symlink(tmp_path / "b" / "s" / "SKILL.md", tmp_path / "a" / "s" / "SKILL.md")
    tally = RateTally(tmp_path)
    tally.add({"relpath": "a/s/SKILL.md", "state": "ok", "findings": []})
    assert set(tally.skills) == {"a"}


def test_an_unsearchable_ancestor_of_the_skill_path_is_counted_not_raised(tmp_path, monkeypatch):
    import errno

    (tmp_path / "locked" / "skill").mkdir(parents=True)

    def denied(path, **kwargs):  # Python 3.11-3.13 let this escape from Path.is_dir
        raise PermissionError(errno.EACCES, "Permission denied", str(path))

    monkeypatch.setattr(Path, "is_dir", denied)
    tally = RateTally(tmp_path)
    tally.add({"relpath": "locked/skill", "state": "error", "findings": []})
    assert tally.summary()["skills"] == 1
