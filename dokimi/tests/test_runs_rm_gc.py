# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""`runs rm` and `runs gc` (#94 R3): what a run is on disk decides what they remove (docs/runs.md,
"Removing runs"); nothing outside the runs root is touched."""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from dokimi import cli
from dokimi.artifacts import save_artifact
from dokimi.runs import rm
from dokimi.runs.scan import scan, timestamp

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "artifacts"
OLD, NEW = "20260901T000000Z-0000000a", "20261101T000000Z-0000000b"


def _fixture(version: str) -> dict:
    return json.loads((FIXTURES / f"{version}.json").read_text(encoding="utf-8"))


def _days_ago(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")


@pytest.fixture
def root(tmp_path, monkeypatch):
    """``results/`` with an old run (eval + answers, 40 days), a new one (arms, 1 day), an old
    question set and an old eval saved before the envelope (no run_id), and a report beside the run."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DOKIMI_RUNS_DIR", raising=False)
    results = tmp_path / "results"

    def save(run_id, kind, data, path):
        monkeypatch.setenv("DOKIMI_RUN_ID", run_id)
        save_artifact(kind, data, results / path)
        monkeypatch.delenv("DOKIMI_RUN_ID")

    save(OLD, "eval", {**_fixture("eval.v1"), "evaluated_at": _days_ago(40)}, "r1/eval/onetbb.json")
    save(OLD, "answers", {**_fixture("answers.v1"), "generated_at": _days_ago(41)},
         "r1/answers/onetbb.json")
    save(NEW, "arms", {**_fixture("arms.v1"), "generated_at": _days_ago(1)}, "arms/onetbb.json")
    save(OLD, "questions", {**_fixture("questions.v2"), "generated_at": _days_ago(50)},
         "r1/questions/demo.json")
    (results / "r1" / "eval" / "onetbb.md").write_text("# report", encoding="utf-8")
    (results / "pre.json").write_text(json.dumps({**_fixture("eval.v1"),
                                                  "evaluated_at": _days_ago(90)}), encoding="utf-8")
    return results


def _run(monkeypatch, capsys, *argv: str):
    monkeypatch.setattr("sys.argv", ["dokimi", "runs", *argv])
    code = 0
    try:
        cli.main()
    except SystemExit as exc:
        code = exc.code
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def _left(results: Path) -> list[str]:
    return sorted(p.relative_to(results).as_posix() for p in results.rglob("*") if p.is_file())


# rm


def test_rm_a_run_id_removes_its_artifacts_and_leaves_other_files_and_directories(
        root, monkeypatch, capsys):
    code, out, _ = _run(monkeypatch, capsys, "rm", OLD)

    assert code == 0
    assert out.splitlines()[0].startswith(f"removed run {OLD}: 2 artifacts, ")
    assert "r1/eval/onetbb.json" in out and "r1/answers/onetbb.json" in out
    assert _left(root) == ["arms/onetbb.json", "pre.json", "r1/eval/onetbb.md",
                           "r1/questions/demo.json"]  # an input kind has no run_id
    assert (root / "r1" / "answers").is_dir()


def test_rm_a_run_id_says_an_unreadable_entry_may_belong_to_the_run_and_leaves_it(
        root, monkeypatch, capsys):
    (root / "r1" / "broken.json").write_text('{"schema_version": "eval.v1", "evaluations": [',
                                             encoding="utf-8")

    code, out, _ = _run(monkeypatch, capsys, "rm", OLD)

    assert code == 0
    assert out.splitlines()[1] == ("unreadable: 1 entry under the root left in place, which may "
                                   "belong to it (first: r1/broken.json: not valid JSON: "
                                   "Expecting value at line 1)")
    assert (root / "r1" / "broken.json").exists()


def test_rm_dry_run_lists_the_run_and_removes_nothing(root, monkeypatch, capsys):
    before = _left(root)

    code, out, _ = _run(monkeypatch, capsys, "rm", OLD, "--dry-run")

    assert code == 0 and out.startswith(f"would remove run {OLD}: 2 artifacts, ")
    assert _left(root) == before


def test_rm_a_path_removes_that_file_only_even_one_that_is_no_artifact(root, monkeypatch, capsys):
    size = (root / "r1" / "eval" / "onetbb.json").stat().st_size
    (root / "broken.json").write_text('{"schema_version": "eval.v1", "evaluations": [',
                                      encoding="utf-8")

    code, out, _ = _run(monkeypatch, capsys, "rm", "r1/eval/onetbb.json")
    assert (code, out) == (0, f"removed r1/eval/onetbb.json ({size:,} bytes)\n")
    assert _run(monkeypatch, capsys, "rm", str(root / "broken.json"))[:2] == (
        0, "removed broken.json (46 bytes)\n")
    assert _run(monkeypatch, capsys, "rm", "results/r1/eval/onetbb.md")[0] == 0

    assert _left(root) == ["arms/onetbb.json", "pre.json", "r1/answers/onetbb.json",
                           "r1/questions/demo.json"]


@pytest.mark.parametrize("address, message", [
    ("../outside.json", "is not under the runs root results/"),
    ("out/x.json", "leads outside the runs root results/"),
    ("r1", "is a directory; runs rm takes a run_id or one file"),
    (".", "is a directory"),
    ("r1/nothing.json", "no file r1/nothing.json under the runs root results/"),
    ("20260101T000000Z-ffffffff", "no artifact there has that run_id"),
])
def test_rm_refuses_what_is_not_one_file_under_the_root_and_removes_nothing(
        root, monkeypatch, capsys, tmp_path, address, message):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "x.json").write_text("{}", encoding="utf-8")
    (tmp_path / "outside.json").write_text("{}", encoding="utf-8")
    try:
        (root / "out").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks need privileges on this platform")
    before = _left(root)

    code, out, err = _run(monkeypatch, capsys, "rm", address)

    assert (code, out) == (2, "")
    assert message in err
    assert _left(root) == before
    assert (outside / "x.json").exists() and (tmp_path / "outside.json").exists()


def test_rm_of_a_symlink_removes_the_link_and_never_its_target(root, monkeypatch, capsys, tmp_path):
    target = tmp_path / "kept.json"
    target.write_text("{}", encoding="utf-8")
    try:
        (root / "link.json").symlink_to(target)
    except OSError:
        pytest.skip("symlinks need privileges on this platform")

    assert _run(monkeypatch, capsys, "rm", "link.json")[0] == 0

    assert not os.path.lexists(root / "link.json")
    assert target.read_text(encoding="utf-8") == "{}"


@pytest.mark.parametrize("address", ["link.json", "results/link.json"])
def test_rm_removes_a_dangling_symlink_however_its_path_is_spelled(root, monkeypatch, capsys,
                                                                   tmp_path, address):
    try:
        (root / "link.json").symlink_to(tmp_path / "gone.json")
    except OSError:
        pytest.skip("symlinks need privileges on this platform")

    code, out, _ = _run(monkeypatch, capsys, "rm", address)

    assert (code, out.split(" (")[0]) == (0, "removed link.json")
    assert not os.path.lexists(root / "link.json")


def test_rm_removes_a_symlink_whose_target_cannot_be_looked_up(root, monkeypatch, capsys, tmp_path):
    locked = tmp_path / "locked"
    locked.mkdir()
    try:
        (root / "link.json").symlink_to(locked / "x.json")
    except OSError:
        pytest.skip("symlinks need privileges on this platform")
    real_stat = os.stat

    def no_search(path, *args, follow_symlinks=True, **kwargs):
        if follow_symlinks and str(path).endswith("link.json"):
            raise PermissionError(13, "Permission denied", str(path))
        return real_stat(path, *args, follow_symlinks=follow_symlinks, **kwargs)

    monkeypatch.setattr(os, "stat", no_search)

    assert _run(monkeypatch, capsys, "rm", "results/link.json")[0] == 0
    assert not os.path.lexists(root / "link.json")


def test_rm_needs_a_runs_root(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DOKIMI_RUNS_DIR", raising=False)

    code, _, err = _run(monkeypatch, capsys, "rm", OLD)

    assert code == 2 and "no runs root: results/ does not exist" in err


def test_rm_json_names_the_run_and_its_paths(root, monkeypatch, capsys):
    size = sum((root / p).stat().st_size for p in ("r1/eval/onetbb.json", "r1/answers/onetbb.json"))

    code, out, _ = _run(monkeypatch, capsys, "rm", OLD, "--json")

    doc = json.loads(out)
    assert (code, doc["kind"], doc["status"]) == (0, "runs rm", "complete")
    data = doc["data"]
    assert (data["run_id"], data["dry_run"], data["not_removed"]) == (OLD, False, [])
    assert data["paths"] == ["r1/eval/onetbb.json", "r1/answers/onetbb.json"]
    assert data["runs"] == [{"run_id": OLD, "kinds": ["answers", "eval"], "paths": data["paths"]}]
    assert (data["size_bytes"], doc["summary"]["paths"]) == (size, 2)


def test_a_file_that_cannot_be_removed_is_named_and_exits_5(root, monkeypatch, capsys):
    real = Path.unlink

    def refuse(self, missing_ok=False):
        if self.name == "onetbb.json" and self.parent.name == "answers":
            raise PermissionError(13, "Permission denied", str(self))
        real(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", refuse)
    size = (root / "r1" / "eval" / "onetbb.json").stat().st_size

    code, out, _ = _run(monkeypatch, capsys, "rm", OLD)
    assert code == 5
    assert out.splitlines()[0] == f"removed run {OLD}: 1 of 2 artifacts, {size:,} bytes"
    assert "r1/answers/onetbb.json" not in out.split("\n\n")[1]  # the table lists what went
    assert "not removed: 1 file (first: r1/answers/onetbb.json: cannot remove: Permission denied)" in out
    assert not (root / "r1" / "eval" / "onetbb.json").exists()  # the others still go

    save_artifact("eval", _fixture("eval.v1"), root / "r2" / "answers" / "onetbb.json")
    code, out, _ = _run(monkeypatch, capsys, "rm", "r2/answers/onetbb.json", "--json")
    doc = json.loads(out)
    assert (code, doc["status"], doc["error"]["code"]) == (5, "failed", "infra")
    assert doc["data"]["not_removed"] == [{"path": "r2/answers/onetbb.json",
                                           "reason": "cannot remove: Permission denied"}]
    assert (doc["data"]["paths"], doc["data"]["size_bytes"], doc["summary"]["paths"]) == ([], 0, 0)
    assert _run(monkeypatch, capsys, "rm", "r2/answers/onetbb.json")[1].startswith("removed nothing\n")
    monkeypatch.setenv("DOKIMI_RUN_ID", "r3")
    save_artifact("eval", _fixture("eval.v1"), root / "r3" / "answers" / "onetbb.json")
    data = json.loads(_run(monkeypatch, capsys, "rm", "r3", "--json")[1])["data"]
    assert (data["runs"], data["paths"], len(data["not_removed"])) == ([], [], 1)


# gc


@pytest.mark.parametrize("refused, headline", [
    ({"answers"}, "removed 1 run older than 30d under results/: 1 of 2 artifacts, {size:,} bytes"),
    ({"answers", "eval"}, "removed 1 run older than 30d under results/: 0 of 2 artifacts, 0 bytes"),
])
def test_gc_counts_the_selected_runs_and_only_the_artifacts_removed(
        root, monkeypatch, capsys, refused, headline):
    real = Path.unlink

    def refuse(self, missing_ok=False):
        if self.parent.name in refused:
            raise PermissionError(13, "Permission denied", str(self))
        real(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", refuse)
    size = (root / "r1" / "eval" / "onetbb.json").stat().st_size

    code, out, _ = _run(monkeypatch, capsys, "gc", "--older-than", "30d")

    assert code == 5
    assert headline.format(size=size) in out.splitlines()
    assert f"not removed: {len(refused)} file" in out


def test_gc_removes_the_runs_older_than_the_age_and_nothing_without_a_run_id(
        root, monkeypatch, capsys):
    code, out, _ = _run(monkeypatch, capsys, "gc", "--older-than", "30d")

    assert code == 0
    table = out.split("\n\n")[0].splitlines()
    assert table[0].split()[:2] == ["RUN", "SCHEMA"]
    assert [row.split()[0] for row in table[1:]] == [OLD, OLD]
    assert "removed 1 run older than 30d under results/: 2 artifacts, " in out
    assert _left(root) == ["arms/onetbb.json", "pre.json", "r1/eval/onetbb.md",
                           "r1/questions/demo.json"]


def test_gc_keeps_a_run_whole_while_any_of_its_artifacts_is_newer(root, monkeypatch, capsys):
    monkeypatch.setenv("DOKIMI_RUN_ID", OLD)
    save_artifact("arms", {**_fixture("arms.v1"), "generated_at": _days_ago(2)},
                  root / "r1" / "arms.json")
    before = _left(root)

    code, out, _ = _run(monkeypatch, capsys, "gc", "--older-than", "30d")

    assert (code, out) == (0, "no run under results/ to remove: none is older than 30d\n")
    assert _left(root) == before


def test_gc_times_an_artifact_that_records_none_by_its_mtime(root, monkeypatch, capsys):
    monkeypatch.setenv("DOKIMI_RUN_ID", "untimed")
    save_artifact("arms", _fixture("arms.v1"), root / "untimed.json")  # the fixture records no time
    monkeypatch.delenv("DOKIMI_RUN_ID")

    assert "untimed.json" not in _run(monkeypatch, capsys, "gc", "--older-than", "1d",
                                      "--dry-run")[1]
    old = time.time() - 3 * 86400
    os.utime(root / "untimed.json", (old, old))
    out = _run(monkeypatch, capsys, "gc", "--older-than", "1d", "--dry-run")[1]

    assert "untimed.json" in out and "* the file's mtime: the artifact records no time" in out


def test_gc_keep_baselines_keeps_the_run_a_saved_baseline_holds(root, monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["dokimi", "baseline", "save", "--from-eval",
                                     "results/r1/eval/onetbb.json", "--name", "main"])
    cli.main()
    capsys.readouterr()
    before = _left(root)

    code, out, _ = _run(monkeypatch, capsys, "gc", "--older-than", "30d", "--keep-baselines")

    assert code == 0
    assert out == ("no run under results/ to remove: none is older than 30d but those a "
                   f"baseline holds\nkept 1 run older than 30d that a baseline holds: {OLD} (main)\n")
    assert _left(root) == before
    data = json.loads(_run(monkeypatch, capsys, "gc", "--older-than", "30d", "--keep-baselines",
                           "--json")[1])["data"]
    assert (data["kept"], data["paths"]) == ([{"run_id": OLD, "baseline": "main"}], [])

    assert _run(monkeypatch, capsys, "gc", "--older-than", "30d")[0] == 0  # the flag is the rule
    assert "r1/eval/onetbb.json" not in _left(root)


@pytest.mark.parametrize("exists_hides_it", [False, True])
def test_gc_keep_baselines_refuses_when_the_manifest_cannot_be_looked_up(
        root, monkeypatch, capsys, exists_hides_it):
    """docs/runs.md: a baseline manifest that cannot be read exits 2 before anything is removed."""
    monkeypatch.setattr("sys.argv", ["dokimi", "baseline", "save", "--from-eval",
                                     "results/r1/eval/onetbb.json", "--name", "main"])
    cli.main()
    capsys.readouterr()
    before = _left(root)
    real_stat = os.stat

    def no_search(path, *args, **kwargs):
        if Path(path).as_posix().endswith("baselines/eval/manifest.json"):
            raise PermissionError(13, "Permission denied", str(path))
        return real_stat(path, *args, **kwargs)

    monkeypatch.setattr(os, "stat", no_search)
    if exists_hides_it:  # Path.exists() on Python 3.14 reads "no permission" as absent
        monkeypatch.setattr(Path, "exists", lambda self, **kw: not self.as_posix().endswith(
            "manifest.json") and os.path.exists(self))

    code, _, err = _run(monkeypatch, capsys, "gc", "--older-than", "30d", "--keep-baselines")

    assert code == 2
    assert "baselines/eval/manifest.json: cannot read: Permission denied" in err
    assert _left(root) == before


@pytest.mark.parametrize("target", ["/nonexistent/share/manifest.json", "manifest.json"])
def test_gc_keep_baselines_refuses_a_manifest_link_to_nothing(root, monkeypatch, capsys, target):
    """docs/runs.md: a baseline manifest that cannot be read exits 2 before anything is removed;
    a dangling link, or one to itself (a loop), is there but cannot be read."""
    monkeypatch.setattr("sys.argv", ["dokimi", "baseline", "save", "--from-eval",
                                     "results/r1/eval/onetbb.json", "--name", "main"])
    cli.main()
    capsys.readouterr()
    manifest = Path("baselines/eval/manifest.json")
    manifest.unlink()
    try:
        manifest.symlink_to(target)
    except OSError:
        pytest.skip("symlinks need privileges on this platform")
    before = _left(root)

    code, _, err = _run(monkeypatch, capsys, "gc", "--older-than", "30d", "--keep-baselines")

    assert code == 2
    assert "baselines/eval/manifest.json: a link to nothing" in err
    assert _left(root) == before


def test_gc_keep_baselines_refuses_when_a_baseline_copy_cannot_be_read(root, monkeypatch, capsys):
    manifest = Path("baselines/eval/manifest.json")
    manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps({"baselines": [{"name": "gone", "path": "baselines/eval/gone.json"}]}),
                        encoding="utf-8")
    before = _left(root)

    code, _, err = _run(monkeypatch, capsys, "gc", "--older-than", "30d", "--keep-baselines")

    assert code == 2
    assert "baseline gone: baselines/eval/gone.json: cannot read" in err
    assert "so the run it holds is not known" in err
    assert _left(root) == before


@pytest.mark.parametrize("age", ["30", "4w", "-1d", "1.5d", "d"])
def test_gc_takes_an_age_in_days_only(root, monkeypatch, capsys, age):
    code, _, err = _run(monkeypatch, capsys, "gc", f"--older-than={age}")

    assert code == 2 and "is not an age in days, such as 30d" in err


def test_gc_needs_an_age(root, monkeypatch, capsys):
    code, _, err = _run(monkeypatch, capsys, "gc")

    assert code == 2 and "--older-than" in err


def test_gc_dry_run_and_json_list_the_runs_and_remove_nothing(root, monkeypatch, capsys):
    before = _left(root)

    code, out, _ = _run(monkeypatch, capsys, "gc", "--older-than", "30d", "--dry-run", "--json")

    doc = json.loads(out)
    assert (code, doc["kind"], doc["status"]) == (0, "runs gc", "complete")
    data = doc["data"]
    assert (data["dry_run"], data["older_than"], data["keep_baselines"]) == (True, "30d", False)
    assert data["runs"] == [{"run_id": OLD, "kinds": ["answers", "eval"],
                             "paths": ["r1/eval/onetbb.json", "r1/answers/onetbb.json"]}]
    assert _left(root) == before
    text = _run(monkeypatch, capsys, "gc", "--older-than", "30d", "--dry-run")[1]
    assert "would remove 1 run older than 30d under results/: 2 artifacts" in text


def test_gc_of_a_missing_default_root_removes_nothing_and_exits_0(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DOKIMI_RUNS_DIR", raising=False)

    code, out, _ = _run(monkeypatch, capsys, "gc", "--older-than", "0d")

    assert (code, out) == (0, "no run under results/ to remove: none is older than 0d\n")


@pytest.mark.parametrize("dry_run", [(), ("--dry-run",)])
def test_gc_removes_nothing_while_an_entry_under_the_root_cannot_be_read(
        root, monkeypatch, capsys, dry_run):
    # the old run's newest artifact, unreadable: the run must not lose its older eval
    monkeypatch.setenv("DOKIMI_RUN_ID", OLD)
    save_artifact("arms", {**_fixture("arms.v1"), "generated_at": _days_ago(1)}, root / "r1" / "arms.json")
    (root / "r1" / "arms.json").write_text('{"schema_version": "arms.v1", "arms": [', encoding="utf-8")
    before = _left(root)

    code, _, err = _run(monkeypatch, capsys, "gc", "--older-than", "30d", *dry_run)

    assert code == 2
    assert ("1 entry under the runs root results/ could not be read (first: r1/arms.json: not valid "
            "JSON") in err and "so it removes nothing" in err
    assert _left(root) == before


def test_gc_keeps_a_run_exactly_as_old_as_the_age_since_it_is_not_older(root):
    """docs/runs.md: gc removes a run whose newest artifact is *older than* AGE."""
    cutoff = max(timestamp(a) for a in scan(root).artifacts if a.run_id == OLD)

    assert rm.plan_gc(root, cutoff, {}).runs == []
    assert [a.run_id for a in rm.plan_gc(root, cutoff + 1, {}).runs[0]] == [OLD, OLD]


def test_parse_age_reads_days():
    assert rm.parse_age("30d") == timedelta(days=30)
    assert rm.parse_age("0d") == timedelta(0)
