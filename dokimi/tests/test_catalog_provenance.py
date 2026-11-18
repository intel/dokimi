# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""``catalog provenance``: matching a skill copy to an upstream version, offline (git checkouts and manifests)."""

from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from dokimi.cli import build_parser
from dokimi.external import upstream
from dokimi.utils import InputError

V1 = (
    "---\nname: pdf\ndescription: Use when reading PDFs and forms.\nlicense: MIT\n---\n"
    "# PDF\nread extract text forms merge split pages tables images metadata\n"
)
V2 = V1.replace("license: MIT\n", "license: MIT\nversion: 2\n").replace(
    "split pages", "split pages rotate crop")
UNRELATED = (
    "---\nname: pdf\ndescription: Kitchen recipes for weeknights.\n---\n"
    "# Cooking\nsimmer onions garlic butter pasta sauce salt pepper oven bake roast\n"
)
NO_LICENCE = V2.replace("license: MIT\n", "")


def _git(cwd: Path, *args: str, date: str | None = None) -> str:
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.com",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.com")
    if date is not None:
        env.update(GIT_AUTHOR_DATE=date, GIT_COMMITTER_DATE=date)
    done = subprocess.run(["git", *args], cwd=cwd, env=env, capture_output=True, text=True, check=True)
    return done.stdout.strip()


def _commit(repo: Path, files: dict[str, str | None], date: str, message: str) -> None:
    for rel, text in files.items():
        target = repo / rel
        if text is None:
            _git(repo, "rm", "-q", "-r", rel)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        _git(repo, "add", rel)
    _git(repo, "commit", "-q", "-m", message, date=date)


@pytest.fixture
def upstream_repo(tmp_path: Path) -> Path:
    """An upstream checkout with three commits: pdf v1 (Jan), pdf v2 (Mar), and an unrelated skill (Apr)."""
    repo = tmp_path / "acme-skills"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _commit(repo, {"skills/pdf/SKILL.md": V1}, "2026-01-01T10:00:00+00:00", "pdf v1")
    _commit(repo, {"skills/pdf/SKILL.md": V2}, "2026-03-01T10:00:00+00:00", "pdf v2")
    _commit(repo, {"skills/cook/SKILL.md": UNRELATED}, "2026-04-01T10:00:00+00:00", "cook")
    return repo


def _copy_root(tmp_path: Path, files: dict[str, str]) -> Path:
    root = tmp_path / "catalog"
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")
    return root


def _run(root: Path, *extra: str, fmt: str = "json") -> dict:
    args = build_parser().parse_args(["catalog", "provenance", "--root", str(root), "--format", fmt, *extra])
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        args.func(args)
    return json.loads(buf.getvalue()) if fmt == "json" else {"text": buf.getvalue()}


def _row(report: dict, skill: str) -> dict:
    return next(r for r in report["skills"] if r["skill"] == skill)


def test_blob_sha_is_git_hash_object(tmp_path: Path) -> None:
    raw = V2.encode("utf-8")
    path = tmp_path / "SKILL.md"
    path.write_bytes(raw)
    expected = _git(tmp_path, "hash-object", "SKILL.md")
    assert upstream.blob_sha(raw) == expected


def test_exact_copy_of_an_old_version_is_behind_by_the_commits_after_it(upstream_repo, tmp_path) -> None:
    root = _copy_root(tmp_path, {"skills/pdf/SKILL.md": V1})
    report = _run(root, "--upstream", str(upstream_repo))
    row = _row(report, "skills/pdf")
    assert row["match"] == "exact"
    assert row["upstream"] == "acme-skills"
    assert row["path"] == "skills/pdf/SKILL.md"
    assert row["at_head"] is False
    assert row["behind_commits"] == 1  # v2 touched the file after v1
    assert row["edited"] is False
    assert row["date"] == "2026-01-01"
    # Head is 2026-04-01 (the cook commit), v1 is 2026-01-01: 90 days.
    assert row["behind_months"] == round(90 / 30.44, 1)


def test_exact_copy_of_head_is_at_head(upstream_repo, tmp_path) -> None:
    root = _copy_root(tmp_path, {"skills/pdf/SKILL.md": V2})
    row = _row(_run(root, "--upstream", str(upstream_repo)), "skills/pdf")
    assert (row["match"], row["at_head"], row["behind_commits"], row["behind_months"]) == ("exact", True, 0, 0.0)


def test_an_edited_copy_matches_by_name_and_similarity(upstream_repo, tmp_path) -> None:
    edited = V2.replace("Use when reading PDFs and forms.", "Use when reading PDFs, forms and scans.")
    root = _copy_root(tmp_path, {"skills/pdf/SKILL.md": edited})
    row = _row(_run(root, "--upstream", str(upstream_repo)), "skills/pdf")
    assert row["match"] == "similar"
    assert row["edited"] is True
    assert row["at_head"] is True  # the closest version is v2, the head
    assert row["jaccard"] is not None and row["jaccard"] >= upstream.SIMILAR_JACCARD


def test_a_copy_below_the_threshold_is_unmatched(upstream_repo, tmp_path) -> None:
    rewritten = V2.split("# PDF")[0] + "# Sailing\nharbour sails tides anchors keel rudder mast hull wind\n"
    root = _copy_root(tmp_path, {"skills/pdf/SKILL.md": rewritten})
    row = _row(_run(root, "--upstream", str(upstream_repo)), "skills/pdf")
    assert row["match"] is None
    assert row["edited"] is None


def test_an_exact_match_does_not_depend_on_the_directory_name(upstream_repo, tmp_path) -> None:
    root = _copy_root(tmp_path, {"plugins/anything/skills/other/SKILL.md": V2})
    row = _row(_run(root, "--upstream", str(upstream_repo)), "plugins/anything/skills/other")
    assert row["match"] == "exact"


def test_the_report_names_the_upstream_head_and_history(upstream_repo, tmp_path) -> None:
    root = _copy_root(tmp_path, {"skills/pdf/SKILL.md": V1})
    report = _run(root, "--upstream", str(upstream_repo))
    (up,) = report["upstreams"]
    assert up["name"] == "acme-skills"
    assert up["history"] == "full"
    assert up["head_date"] == "2026-04-01"
    assert report["summary"]["exact"] == 1
    assert report["summary"]["median_months_behind"] == round(90 / 30.44, 1)


def test_text_output_has_a_header_and_one_line_per_skill(upstream_repo, tmp_path) -> None:
    root = _copy_root(tmp_path, {"skills/pdf/SKILL.md": V1})
    text = _run(root, "--upstream", str(upstream_repo), fmt="text")["text"]
    lines = text.splitlines()
    assert lines[0].startswith("# upstream acme-skills: head ")
    assert lines[1].startswith("# skills (1 under ")
    assert lines[2] == "skill\tmatch\tupstream\tversion\tbehind\tedited"
    assert lines[3].split("\t")[:2] == ["skills/pdf", "exact"]


def test_a_shallow_clone_is_said_to_see_only_its_own_history(upstream_repo, tmp_path) -> None:
    shallow = tmp_path / "shallow"
    subprocess.run(["git", "clone", "-q", "--depth", "1", f"file://{upstream_repo}", str(shallow)], check=True,
                   capture_output=True)
    root = _copy_root(tmp_path, {"skills/pdf/SKILL.md": V1})  # v1 is not in a depth-1 clone
    report = _run(root, "--upstream", str(shallow))
    assert report["upstreams"][0]["history"] == "shallow"
    row = _row(report, "skills/pdf")
    assert row["match"] == "similar"  # v1 is not in the clone, so no exact match; the head is the similar one
    assert row["edited"] is True


def test_a_manifest_matches_by_its_content_and_checks_nothing_it_lacks(tmp_path) -> None:
    manifest = tmp_path / "acme.json"
    manifest.write_text(json.dumps({
        "schema": "dokimi.upstream.v1",
        "name": "acme",
        "head": {"commit": "abc123", "date": "2026-04-01T00:00:00+00:00"},
        "skills": [
            {"path": "skills/pdf/SKILL.md", "content": V1, "commit": "c1", "date": "2026-01-01T00:00:00+00:00"},
            {"path": "skills/pdf/SKILL.md", "content": V2, "head": True, "date": "2026-03-01T00:00:00+00:00"},
        ],
    }), encoding="utf-8")
    root = _copy_root(tmp_path, {"skills/pdf/SKILL.md": V1})
    report = _run(root, "--upstream-manifest", str(manifest))
    row = _row(report, "skills/pdf")
    assert (row["match"], row["upstream"], row["edited"]) == ("exact", "acme", False)
    assert row["behind_commits"] is None  # a manifest says no commit count
    assert row["behind_months"] == round(90 / 30.44, 1)
    assert report["upstreams"][0]["history"] == "manifest"


def test_a_manifest_with_the_wrong_schema_is_refused(tmp_path) -> None:
    manifest = tmp_path / "bad.json"
    manifest.write_text(json.dumps({"schema": "other", "name": "x", "skills": []}), encoding="utf-8")
    root = _copy_root(tmp_path, {"skills/pdf/SKILL.md": V1})
    with pytest.raises(InputError, match="dokimi.upstream.v1"):
        _run(root, "--upstream-manifest", str(manifest))


def test_needs_an_upstream(tmp_path) -> None:
    root = _copy_root(tmp_path, {"skills/pdf/SKILL.md": V1})
    with pytest.raises(InputError, match="needs --upstream"):
        _run(root)


def test_two_upstreams_with_one_name_are_refused(upstream_repo, tmp_path) -> None:
    twin = tmp_path / "other" / "acme-skills"
    shutil.copytree(upstream_repo, twin)
    root = _copy_root(tmp_path, {"skills/pdf/SKILL.md": V1})
    with pytest.raises(InputError, match="named 'acme-skills'"):
        _run(root, "--upstream", str(upstream_repo), "--upstream", str(twin))


def test_a_directory_that_is_not_a_git_checkout_is_refused(tmp_path) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    root = _copy_root(tmp_path, {"skills/pdf/SKILL.md": V1})
    with pytest.raises(InputError, match="not a git checkout"):
        _run(root, "--upstream", str(plain))


def test_the_first_upstream_wins_a_tie(upstream_repo, tmp_path) -> None:
    second = tmp_path / "mirror" / "acme-mirror"
    shutil.copytree(upstream_repo, second)
    root = _copy_root(tmp_path, {"skills/pdf/SKILL.md": V2})
    row = _row(_run(root, "--upstream", str(second), "--upstream", str(upstream_repo)), "skills/pdf")
    assert row["upstream"] == "acme-mirror"


def test_a_path_with_a_space_and_non_ascii_is_read_as_written(tmp_path) -> None:
    repo = tmp_path / "up"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _commit(repo, {"naïve skills/pdf/SKILL.md": V2}, "2026-02-01T10:00:00+00:00", "odd path")
    root = _copy_root(tmp_path, {"skills/pdf/SKILL.md": V2})
    row = _row(_run(root, "--upstream", str(repo)), "skills/pdf")
    assert row["match"] == "exact"
    assert row["path"] == "naïve skills/pdf/SKILL.md"


def test_unloaded_skills_are_matched_by_their_bytes_too(tmp_path) -> None:
    bare = "---\nname: bare\n---\nbody words here\n"  # no description: the skill does not load
    repo = tmp_path / "acme-skills"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _commit(repo, {"skills/pdf/SKILL.md": V2, "skills/bare/SKILL.md": bare}, "2026-03-01T10:00:00+00:00", "both")
    root = _copy_root(tmp_path, {
        "skills/pdf/SKILL.md": V2,
        "skills/bare/SKILL.md": bare,
        "skills/broken/SKILL.md": "---\nname: [unclosed\n---\nbody\n",
    })
    report = _run(root, "--upstream", str(repo))
    assert [r["skill"] for r in report["skills"]] == ["skills/bare", "skills/broken", "skills/pdf"]
    assert [r["match"] for r in report["skills"]] == ["exact", None, "exact"]
    assert report["summary"]["skills"] == 3


def test_a_plain_directory_inside_a_repo_is_not_a_checkout(upstream_repo, tmp_path) -> None:
    plain = upstream_repo / "vendor" / "acme"  # untracked, but git finds the enclosing repository from here
    plain.mkdir(parents=True)
    (plain / "SKILL.md").write_text(V2, encoding="utf-8")
    root = _copy_root(tmp_path, {"skills/pdf/SKILL.md": V2})
    with pytest.raises(InputError, match="not a git checkout"):
        _run(root, "--upstream", str(plain))


def test_a_manifest_mixing_naive_and_zoned_dates_still_gives_the_months(tmp_path) -> None:
    manifest = tmp_path / "mixed.json"
    manifest.write_text(json.dumps({
        "schema": "dokimi.upstream.v1",
        "name": "acme",
        "head": {"commit": "abc123", "date": "2026-04-01T00:00:00+00:00"},
        "skills": [{"path": "skills/pdf/SKILL.md", "content": V1, "commit": "c1", "date": "2026-01-01"}],
    }), encoding="utf-8")
    root = _copy_root(tmp_path, {"skills/pdf/SKILL.md": V1})
    row = _row(_run(root, "--upstream-manifest", str(manifest)), "skills/pdf")
    assert row["match"] == "exact"
    assert row["behind_months"] == round(90 / 30.44, 1)


def test_an_exact_copy_matches_an_upstream_in_a_sha256_repository(tmp_path: Path) -> None:
    repo = tmp_path / "acme-skills"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main", "--object-format=sha256")
    _commit(repo, {"skills/pdf/SKILL.md": V1}, "2026-01-01T10:00:00+00:00", "pdf v1")
    _commit(repo, {"skills/pdf/SKILL.md": V2}, "2026-03-01T10:00:00+00:00", "pdf v2")
    root = _copy_root(tmp_path, {"skills/pdf/SKILL.md": V1})
    row = _row(_run(root, "--upstream", str(repo)), "skills/pdf")
    assert (row["match"], row["edited"], row["date"], row["behind_commits"]) == ("exact", False, "2026-01-01", 1)


def test_a_version_merged_into_the_history_is_matched_and_counted(tmp_path: Path) -> None:
    repo = tmp_path / "acme-skills"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    branch_a = V1.replace("license: MIT", "license: Apache-2.0")  # on branch a, Feb 1
    main_b = V1.replace("# PDF\n", "# PDF Tools\n")  # on main, Feb 5
    merged = branch_a.replace("# PDF\n", "# PDF Tools\n")  # the merge, Feb 10
    later = merged.replace("read extract text", "read extract text pages")  # main, Mar 1
    _commit(repo, {"skills/pdf/SKILL.md": V1}, "2026-01-01T10:00:00+00:00", "pdf v1")
    _git(repo, "checkout", "-q", "-b", "branch-a")
    _commit(repo, {"skills/pdf/SKILL.md": branch_a}, "2026-02-01T10:00:00+00:00", "license")
    _git(repo, "checkout", "-q", "main")
    _commit(repo, {"skills/pdf/SKILL.md": main_b}, "2026-02-05T10:00:00+00:00", "title")
    _git(repo, "merge", "-q", "--no-ff", "-m", "merge branch-a", "branch-a", date="2026-02-10T10:00:00+00:00")
    _commit(repo, {"skills/pdf/SKILL.md": later}, "2026-03-01T10:00:00+00:00", "more")
    merged_copy = _copy_root(tmp_path, {"skills/pdf/SKILL.md": merged})
    row = _row(_run(merged_copy, "--upstream", str(repo)), "skills/pdf")
    assert (row["match"], row["edited"], row["date"], row["behind_commits"]) == ("exact", False, "2026-02-10", 1)
    branch_copy = _copy_root(tmp_path / "again", {"skills/pdf/SKILL.md": branch_a})
    row = _row(_run(branch_copy, "--upstream", str(repo)), "skills/pdf")
    assert (row["match"], row["date"], row["behind_commits"]) == ("exact", "2026-02-01", 3)


def test_duplicate_manifest_bytes_match_the_newest_version(tmp_path) -> None:
    manifest = tmp_path / "dup.json"
    manifest.write_text(json.dumps({
        "schema": "dokimi.upstream.v1",
        "name": "acme",
        "head": {"commit": "abc123", "date": "2026-02-01T00:00:00+00:00"},
        "skills": [
            {"path": "skills/pdf/SKILL.md", "content": V1, "commit": "old", "date": "2025-01-01T00:00:00+00:00"},
            {"path": "skills/pdf/SKILL.md", "content": V1, "commit": "new", "date": "2026-01-01T00:00:00+00:00"},
        ],
    }), encoding="utf-8")
    root = _copy_root(tmp_path, {"skills/pdf/SKILL.md": V1})
    row = _row(_run(root, "--upstream-manifest", str(manifest)), "skills/pdf")
    assert (row["match"], row["date"], row["commit"]) == ("exact", "2026-01-01", "new")
    assert row["behind_months"] == round(31 / 30.44, 1)


def test_upstreams_with_the_same_bytes_tie_to_the_first_one_listed_not_the_newer(tmp_path) -> None:
    def manifest(name: str, date: str) -> Path:
        path = tmp_path / f"{name}.json"
        path.write_text(json.dumps({
            "schema": "dokimi.upstream.v1",
            "name": name,
            "head": {"commit": "abc123", "date": "2026-06-01T00:00:00+00:00"},
            "skills": [{"path": "skills/pdf/SKILL.md", "content": V1, "commit": name, "date": date}],
        }), encoding="utf-8")
        return path

    first = manifest("acme", "2026-01-01T00:00:00+00:00")
    second = manifest("zeta", "2026-06-01T00:00:00+00:00")
    root = _copy_root(tmp_path, {"skills/pdf/SKILL.md": V1})
    row = _row(_run(root, "--upstream-manifest", str(first), "--upstream-manifest", str(second)), "skills/pdf")
    assert (row["match"], row["upstream"]) == ("exact", "acme")


def test_a_copy_checked_out_with_crlf_is_exact_not_edited(upstream_repo, tmp_path) -> None:
    vendor = tmp_path / "vendor"
    vendor.mkdir()
    _git(vendor, "init", "-q", "-b", "main")
    _git(vendor, "config", "core.autocrlf", "false")
    (vendor / "skills/pdf").mkdir(parents=True)
    (vendor / "skills/pdf/SKILL.md").write_bytes(V1.encode("utf-8"))
    _git(vendor, "add", "skills/pdf/SKILL.md")
    _git(vendor, "commit", "-q", "-m", "vendor pdf v1", date="2026-02-01T10:00:00+00:00")
    _git(tmp_path, "-c", "core.autocrlf=true", "clone", "-q", str(vendor), "copy")
    raw = (tmp_path / "copy" / "skills/pdf/SKILL.md").read_bytes()
    assert b"\r\n" in raw
    row = _row(_run(tmp_path / "copy", "--upstream", str(upstream_repo)), "skills/pdf")
    assert (row["match"], row["edited"], row["behind_commits"]) == ("exact", False, 1)


def test_a_version_dated_after_head_is_not_negative_months_behind(tmp_path) -> None:
    text = V1.replace("license: MIT\n", "license: MIT\nmetadata:\n  version: 3\n")
    manifest = tmp_path / "skew.json"
    manifest.write_text(json.dumps({
        "schema": "dokimi.upstream.v1",
        "name": "acme",
        "head": {"commit": "abc123", "date": "2026-04-01T00:00:00+00:00"},
        "skills": [{"path": "skills/pdf/SKILL.md", "content": text, "commit": "c1", "date": "2026-06-01T00:00:00+00:00"}],
    }), encoding="utf-8")
    root = _copy_root(tmp_path, {"skills/pdf/SKILL.md": text})
    report = _run(root, "--upstream-manifest", str(manifest))
    row = _row(report, "skills/pdf")
    assert (row["match"], row["at_head"], row["version"]) == ("exact", False, "3")
    assert row["behind_months"] == 0.0
    assert report["summary"]["median_months_behind"] == 0.0


def _manifest(tmp_path: Path, skills: list[dict], head: object = None, name: str = "acme") -> Path:
    path = tmp_path / f"{name}.json"
    path.write_text(json.dumps({
        "schema": "dokimi.upstream.v1", "name": name,
        "head": {"commit": "abc123", "date": "2026-06-01T00:00:00+00:00"} if head is None else head,
        "skills": skills,
    }), encoding="utf-8")
    return path


def _entry(content: str = V1, **extra) -> dict:
    return {"path": "skills/pdf/SKILL.md", "content": content, **extra}


def test_a_clean_no_ff_merge_counts_a_branch_edit_once(tmp_path: Path) -> None:
    repo = tmp_path / "acme-skills"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _commit(repo, {"skills/pdf/SKILL.md": V1}, "2026-01-01T10:00:00+00:00", "pdf v1")
    _git(repo, "checkout", "-q", "-b", "edit")
    _commit(repo, {"skills/pdf/SKILL.md": V2}, "2026-02-01T10:00:00+00:00", "pdf v2")
    _git(repo, "checkout", "-q", "main")
    _git(repo, "merge", "-q", "--no-ff", "-m", "merge edit", "edit", date="2026-02-10T10:00:00+00:00")
    assert _git(repo, "log", "--format=%H", "--", "skills/pdf/SKILL.md").count("\n") == 1
    row = _row(_run(_copy_root(tmp_path, {"skills/pdf/SKILL.md": V1}), "--upstream", str(repo)), "skills/pdf")
    assert (row["match"], row["behind_commits"]) == ("exact", 1)


def test_log_show_root_false_in_the_checkout_keeps_the_first_version(upstream_repo, tmp_path) -> None:
    _git(upstream_repo, "config", "log.showRoot", "false")
    row = _row(_run(_copy_root(tmp_path, {"skills/pdf/SKILL.md": V1}), "--upstream", str(upstream_repo)), "skills/pdf")
    assert (row["match"], row["edited"], row["behind_commits"]) == ("exact", False, 1)


@pytest.mark.skipif(os.name == "nt", reason="the marker program is a shell script")
def test_reading_a_checkout_does_not_run_its_signature_program(tmp_path: Path) -> None:
    repo = tmp_path / "acme-skills"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    marker = tmp_path / "ran"
    program = tmp_path / "gpg.sh"
    program.write_text(f"#!/bin/sh\ntouch {marker}\n", encoding="utf-8")
    program.chmod(0o755)
    _commit(repo, {"skills/pdf/SKILL.md": V1}, "2026-01-01T10:00:00+00:00", "pdf v1")
    # a commit carrying a gpgsig header, which log.showSignature hands to gpg.program
    tree = _git(repo, "rev-parse", "HEAD^{tree}")
    raw = f"tree {tree}\nauthor t <t@example.com> 1767262800 +0000\ncommitter t <t@example.com> 1767262800 +0000\n"
    raw += "gpgsig -----BEGIN PGP SIGNATURE-----\n \n x\n -----END PGP SIGNATURE-----\n\nsigned\n"
    env = dict(os.environ)
    signed = subprocess.run(["git", "hash-object", "-t", "commit", "-w", "--stdin", "--literally"], cwd=repo, env=env,
                            input=raw, capture_output=True, text=True, check=True).stdout.strip()
    _git(repo, "reset", "-q", "--hard", signed)
    _git(repo, "config", "log.showSignature", "true")
    _git(repo, "config", "gpg.program", str(program))
    row = _row(_run(_copy_root(tmp_path, {"skills/pdf/SKILL.md": V1}), "--upstream", str(repo)), "skills/pdf")
    assert row["match"] == "exact"
    assert not marker.exists()


def test_a_blob_the_clone_lacks_is_said_not_passed_as_full_history(upstream_repo, tmp_path) -> None:
    sha = upstream.blob_sha(V1.encode("utf-8"))
    (upstream_repo / ".git/objects" / sha[:2] / sha[2:]).unlink()
    report = _run(_copy_root(tmp_path, {"skills/pdf/SKILL.md": V1}), "--upstream", str(upstream_repo))
    assert report["upstreams"][0]["history"] == "partial"
    assert report["upstreams"][0]["missing_blobs"] == 1
    text = _run(tmp_path / "catalog", "--upstream", str(upstream_repo), fmt="text")["text"]
    assert "1 blobs the clone does not hold" in text


def test_a_deleted_skill_in_a_sha256_repository_is_not_a_missing_blob(tmp_path: Path) -> None:
    repo = tmp_path / "acme-skills"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main", "--object-format=sha256")
    _commit(repo, {"skills/pdf/SKILL.md": V1}, "2026-01-01T10:00:00+00:00", "pdf v1")
    _commit(repo, {"skills/pdf/SKILL.md": None}, "2026-02-01T10:00:00+00:00", "drop pdf")
    _commit(repo, {"skills/cook/SKILL.md": UNRELATED}, "2026-03-01T10:00:00+00:00", "cook")
    report = _run(_copy_root(tmp_path, {"skills/pdf/SKILL.md": V1}), "--upstream", str(repo))
    assert report["upstreams"][0]["history"] == "full"
    assert _row(report, "skills/pdf")["match"] == "exact"


@pytest.mark.parametrize("where", ["head", "entry"])
def test_a_non_string_commit_in_a_manifest_is_refused(tmp_path, where) -> None:
    head = {"commit": 123, "date": "2026-06-01T00:00:00+00:00"} if where == "head" else None
    entry = _entry(commit=123 if where == "entry" else "c1")
    root = _copy_root(tmp_path, {"skills/pdf/SKILL.md": V1})
    with pytest.raises(InputError, match='"commit".*must be a string or null'):
        _run(root, "--upstream-manifest", str(_manifest(tmp_path, [entry], head)))


def test_a_non_string_date_or_non_boolean_head_in_a_manifest_is_refused(tmp_path) -> None:
    root = _copy_root(tmp_path, {"skills/pdf/SKILL.md": V1})
    with pytest.raises(InputError, match='"date".*must be a string or null'):
        _run(root, "--upstream-manifest", str(_manifest(tmp_path, [_entry(date=5)])))
    for value in ("true", 1):
        with pytest.raises(InputError, match='"head".*must be true or false'):
            _run(root, "--upstream-manifest", str(_manifest(tmp_path, [_entry(head=value)])))
    with pytest.raises(InputError, match='"head" must be an object'):
        _run(root, "--upstream-manifest", str(_manifest(tmp_path, [_entry()], head="true")))


def test_a_missing_upstream_directory_is_an_input_error(tmp_path) -> None:
    root = _copy_root(tmp_path, {"skills/pdf/SKILL.md": V1})
    with pytest.raises(InputError, match="is not a directory"):
        _run(root, "--upstream", str(tmp_path / "does-not-exist"))


def test_an_edited_copy_takes_the_newest_of_two_same_content_manifest_versions(tmp_path) -> None:
    older = _entry(commit="old", date="2025-01-01T00:00:00+00:00")
    newer = _entry(commit="new", date="2026-01-01T00:00:00+00:00")
    root = _copy_root(tmp_path, {"skills/pdf/SKILL.md": V1 + "scans\n"})
    for listing in ([older, newer], [newer, older]):
        manifest = _manifest(tmp_path, listing)
        row = _row(_run(root, "--upstream-manifest", str(manifest)), "skills/pdf")
        assert (row["match"], row["commit"], row["behind_months"]) == ("similar", "new", round(151 / 30.44, 1))


def test_a_copy_of_a_renamed_skill_says_its_path_is_gone_at_head(tmp_path: Path) -> None:
    repo = tmp_path / "acme-skills"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _commit(repo, {"skills/old/SKILL.md": V1}, "2026-01-01T10:00:00+00:00", "old v1")
    _commit(repo, {"skills/old/SKILL.md": None, "skills/renamed/SKILL.md": V2}, "2026-02-01T10:00:00+00:00", "rename")
    _commit(repo, {"skills/renamed/SKILL.md": V2 + "more\n"}, "2026-03-01T10:00:00+00:00", "edit")
    row = _row(_run(_copy_root(tmp_path, {"skills/pdf/SKILL.md": V1}), "--upstream", str(repo)), "skills/pdf")
    assert (row["path"], row["behind_commits"], row["removed_at_head"]) == ("skills/old/SKILL.md", 1, True)
    text = _run(tmp_path / "catalog", "--upstream", str(repo), fmt="text")["text"]
    assert "1 commits, 1.9 mo, path removed at head" in text


def test_a_copy_without_a_frontmatter_name_is_not_matched_by_its_directory(upstream_repo, tmp_path) -> None:
    nameless = V1.replace("name: pdf\n", "") + "scans\n"
    root = _copy_root(tmp_path, {"skills/pdf/SKILL.md": nameless})
    assert _row(_run(root, "--upstream", str(upstream_repo)), "skills/pdf")["match"] is None


def test_path_quoting_covers_carriage_return_and_a_line_separator_in_a_path(tmp_path: Path) -> None:
    assert upstream._unquote('"a\\rb\\tc"') == "a\rb\tc"
    repo = tmp_path / "acme-skills"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _commit(repo, {"skills/a\u2028b/SKILL.md": V1}, "2026-01-01T10:00:00+00:00", "v1")
    _commit(repo, {"skills/a\u2028b/SKILL.md": V2}, "2026-02-01T10:00:00+00:00", "v2")
    row = _row(_run(_copy_root(tmp_path, {"skills/pdf/SKILL.md": V1}), "--upstream", str(repo)), "skills/pdf")
    assert (row["match"], row["path"], row["behind_commits"]) == ("exact", "skills/a\u2028b/SKILL.md", 1)


def test_the_ranking_and_the_summary_are_pinned(tmp_path) -> None:
    far = V1.replace("name: pdf", "name: cook")
    head_text = "---\nname: tool\ndescription: x\n---\n" + " ".join(f"w{i}" for i in range(4))  # 8 words
    exactly = head_text + " w4 w5"  # all 8 shared of 10: Jaccard 0.8 on the dot
    old_text = head_text.replace("w3", "w9")
    first = _manifest(tmp_path, [
        {"path": "t/SKILL.md", "content": head_text, "head": True},
        {"path": "t/SKILL.md", "content": old_text, "commit": "c0", "date": "2026-01-01T00:00:00+00:00"},
        _entry(V1, commit="p1", date="2026-04-01T00:00:00+00:00"),
    ], name="first")
    second = _manifest(tmp_path, [{"path": "t/SKILL.md", "content": head_text, "head": True}], name="second")
    root = _copy_root(tmp_path, {
        "skills/exact/SKILL.md": V1,
        "skills/similar/SKILL.md": exactly,
        "skills/unmatched/SKILL.md": far,
        "skills/athead/SKILL.md": head_text,
    })
    report = _run(root, "--upstream-manifest", str(first), "--upstream-manifest", str(second))
    rows = {r["skill"].split("/")[1]: r for r in report["skills"]}
    assert (rows["exact"]["match"], rows["exact"]["upstream"], rows["exact"]["at_head"]) == ("exact", "first", False)
    assert rows["exact"]["behind_months"] == round(61 / 30.44, 1)
    # the same bytes at the head of both upstreams: the earlier one listed
    assert (rows["athead"]["match"], rows["athead"]["upstream"], rows["athead"]["at_head"]) == ("exact", "first", True)
    # Jaccard exactly 0.8 is similar; the head version is preferred to the older one, and the first upstream wins
    assert (rows["similar"]["match"], rows["similar"]["jaccard"], rows["similar"]["upstream"]) == ("similar", 0.8, "first")
    assert rows["similar"]["at_head"] is True and rows["similar"]["edited"] is True
    assert rows["unmatched"]["match"] is None
    assert report["summary"] == {
        "skills": 4, "exact": 2, "similar": 1, "unmatched": 1, "edited": 1, "at_head": 2,
        "median_months_behind": round(61 / 30.44, 1),
    }


def test_the_text_columns_are_pinned(upstream_repo, tmp_path) -> None:
    root = _copy_root(tmp_path, {"skills/pdf/SKILL.md": V1, "skills/cook/SKILL.md": UNRELATED.replace("pdf", "zzz")})
    lines = _run(root, "--upstream", str(upstream_repo), fmt="text")["text"].splitlines()
    assert lines[2] == "skill\tmatch\tupstream\tversion\tbehind\tedited"
    cook, pdf = lines[3:]
    assert cook.split("\t")[:2] == ["skills/cook", "unmatched"] and cook.split("\t")[4:] == ["-", "-"]
    fields = pdf.split("\t")
    assert fields[:3] == ["skills/pdf", "exact", "acme-skills:skills/pdf/SKILL.md"]
    assert fields[3].endswith(" 2026-01-01") and fields[4:] == ["1 commits, 3.0 mo", "no"]


def test_a_crlf_copy_reads_its_own_version_not_the_later_lf_one(tmp_path: Path) -> None:
    repo = tmp_path / "acme-skills"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    crlf = V1.replace("\n", "\r\n").encode("utf-8")
    (repo / "skills/pdf").mkdir(parents=True)
    (repo / "skills/pdf/SKILL.md").write_bytes(crlf)
    _git(repo, "add", "skills/pdf/SKILL.md")
    _git(repo, "commit", "-q", "-m", "v1 crlf", date="2026-01-01T10:00:00+00:00")
    _commit(repo, {"skills/pdf/SKILL.md": V1}, "2026-03-01T10:00:00+00:00", "v1 lf")
    root = tmp_path / "catalog/skills/pdf"
    root.mkdir(parents=True)
    (root / "SKILL.md").write_bytes(crlf)
    row = _row(_run(tmp_path / "catalog", "--upstream", str(repo)), "skills/pdf")
    assert (row["match"], row["at_head"], row["behind_commits"]) == ("exact", False, 1)
