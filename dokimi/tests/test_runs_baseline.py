# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""`runs baseline save|list|rm` (#94 §2.3, R3): a baseline is a named pointer to a run, and
``--expect-previous`` makes a save a compare-and-swap (docs/runs.md, "Baselines")."""

from __future__ import annotations

import contextlib
import json
import os
import re
import subprocess  # nosec B404 - two real processes race for one pointer
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from dokimi import cli
from dokimi.artifacts import save_artifact
from dokimi.runs import baseline
from tests.waiting import wait_until

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "artifacts"
PROJECT = Path(__file__).resolve().parents[1]
A, B, C = "20261101T000000Z-0000000a", "20261102T000000Z-0000000b", "20261103T000000Z-0000000c"


@pytest.fixture
def root(tmp_path, monkeypatch):
    """``results/`` with two runs, A (eval + answers) and B (arms)."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DOKIMI_RUNS_DIR", raising=False)
    results = tmp_path / "results"
    for run_id, kind, version, path in ((A, "eval", "eval.v1", "a/eval.json"),
                                        (A, "answers", "answers.v1", "a/answers.json"),
                                        (B, "arms", "arms.v1", "b/arms.json")):
        monkeypatch.setenv("DOKIMI_RUN_ID", run_id)
        data = json.loads((FIXTURES / f"{version}.json").read_text(encoding="utf-8"))
        save_artifact(kind, data, results / path)
    monkeypatch.delenv("DOKIMI_RUN_ID")
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


def _pointer(results: Path, name: str) -> str | None:
    path = results / ".baselines" / name.replace("/", "%2F")
    return path.read_text(encoding="utf-8") if path.exists() else None


def test_save_list_rm_a_namespaced_baseline(root, monkeypatch, capsys):
    code, out, _ = _run(monkeypatch, capsys, "baseline", "save", "skill/a@main", A)
    assert (code, out) == (0, f"saved baseline skill/a@main -> {A} (2 artifacts); before: none\n")
    assert _pointer(root, "skill/a@main") == A + "\n"  # docs/runs.md: one line, the run_id

    code, out, _ = _run(monkeypatch, capsys, "baseline", "list")
    assert code == 0
    assert re.fullmatch(rf"NAME +RUN +SAVED \(UTC\)\nskill/a@main  {A}  \d{{4}}-\d\d-\d\d \d\d:\d\d\n"
                        r"\n1 baseline under results/\n", out)

    code, out, _ = _run(monkeypatch, capsys, "baseline", "rm", "skill/a@main")
    assert (code, out) == (0, f"removed baseline skill/a@main (it held {A}); the run stays\n")
    assert _pointer(root, "skill/a@main") is None
    assert (root / "a" / "eval.json").exists()
    assert _run(monkeypatch, capsys, "baseline", "list")[1] == "0 baselines under results/\n"


def test_list_json_names_each_baseline_and_its_run(root, monkeypatch, capsys):
    _run(monkeypatch, capsys, "baseline", "save", "main", B)
    _run(monkeypatch, capsys, "baseline", "save", "skill/a@main", A)

    doc = json.loads(_run(monkeypatch, capsys, "baseline", "list", "--json")[1])

    assert (doc["kind"], doc["status"]) == ("runs baseline list", "complete")
    assert [(b["name"], b["run_id"]) for b in doc["data"]["baselines"]] == [("main", B),
                                                                            ("skill/a@main", A)]
    assert all(re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", b["saved_at"])
               for b in doc["data"]["baselines"])


def test_a_save_without_expect_previous_moves_the_pointer_and_says_what_it_held(
        root, monkeypatch, capsys):
    _run(monkeypatch, capsys, "baseline", "save", "main", A)

    code, out, _ = _run(monkeypatch, capsys, "baseline", "save", "main", B, "--json")

    data = json.loads(out)["data"]
    assert (code, data["run_id"], data["previous"], data["artifacts"]) == (0, B, A, 1)
    assert data["path"] == ".baselines/main"
    assert _pointer(root, "main") == B + "\n"


def test_expect_previous_saves_while_the_pointer_holds_that_run(root, monkeypatch, capsys):
    _run(monkeypatch, capsys, "baseline", "save", "main", A)

    code, out, _ = _run(monkeypatch, capsys, "baseline", "save", "main", B, "--expect-previous", A)

    assert (code, out) == (0, f"saved baseline main -> {B} (1 artifact); before: {A}\n")
    assert _pointer(root, "main") == B + "\n"


@pytest.mark.parametrize("held", [B, None])
def test_expect_previous_that_does_not_hold_exits_4_and_leaves_the_pointer(
        root, monkeypatch, capsys, held):
    # #94 §2.6: a stale baseline is `invalid`, exit 4
    if held:
        _run(monkeypatch, capsys, "baseline", "save", "main", held)

    code, out, err = _run(monkeypatch, capsys, "baseline", "save", "main", A, "--expect-previous", C)

    assert (code, out) == (4, "")
    holds = f"holds {held}" if held else "does not exist"
    assert err == (f"Error: baseline main {holds}, not {C} as --expect-previous says: another save "
                   "moved it, so nothing was saved\n")
    assert _pointer(root, "main") == (held + "\n" if held else None)
    code, out, _ = _run(monkeypatch, capsys, "baseline", "save", "main", A, "--expect-previous", C,
                        "--json")
    doc = json.loads(out)
    assert (code, doc["status"], doc["verdict"], doc["reason"]) == (4, "failed", "invalid",
                                                                    "stale_baseline")
    assert (doc["data"]["expected"], doc["data"]["found"]) == (C, held)


def test_expect_previous_against_a_pointer_that_holds_no_run_id_exits_4(root, monkeypatch, capsys):
    (root / ".baselines").mkdir()
    (root / ".baselines" / "main").write_text("", encoding="utf-8")

    code, _, err = _run(monkeypatch, capsys, "baseline", "save", "main", A, "--expect-previous", B)

    assert (code, err) == (4, f"Error: baseline main holds no run id, not {B} as --expect-previous "
                              "says: another save moved it, so nothing was saved\n")
    assert _pointer(root, "main") == ""


@contextlib.contextmanager
def _cli(root: Path, *argvs: list[str]) -> Iterator[list[subprocess.Popen]]:
    """``cli.py runs ARGV`` for each argv, at once, on ``root``; any still running at the end is
    killed, so a failed test does not leave them to the next one."""
    env = {**os.environ, "DOKIMI_RUNS_DIR": str(root)}
    env.pop("DOKIMI_RUN_ID", None)
    procs = [subprocess.Popen([sys.executable, str(PROJECT / "cli.py"), "runs", *argv],  # nosec B603
                              env=env, cwd=PROJECT, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              text=True) for argv in argvs]
    try:
        yield procs
    finally:
        for proc in procs:
            if proc.poll() is None:
                proc.kill()
            proc.communicate()


def _wait_on_the_lock(root: Path, waiters: int) -> None:
    """Until ``waiters`` processes wait on ``.baselines/.lock``, as ``/proc/locks`` shows (Linux)."""
    waiting = re.compile(rf"->.*:{(root / '.baselines' / '.lock').stat().st_ino} ")
    wait_until(lambda: sum(1 for line in Path("/proc/locks").read_text().splitlines()
                           if waiting.search(line)) == waiters,
               60, what=f"{waiters} process(es) to wait on the lock")


@pytest.mark.skipif(not Path("/proc/locks").exists(), reason="waiting on a lock is seen in /proc/locks (Linux)")
def test_two_processes_that_expect_the_same_run_keep_one_winner(root, monkeypatch):
    """Both wait on the lock while the test holds it, then race: one saves, the other exits 4."""
    monkeypatch.setenv("DOKIMI_RUN_ID", C)
    save_artifact("arms", json.loads((FIXTURES / "arms.v1.json").read_text(encoding="utf-8")),
                  root / "c" / "arms.json")
    monkeypatch.delenv("DOKIMI_RUN_ID")
    baseline.save(root, "main", A, None)
    with contextlib.ExitStack() as lock:
        lock.enter_context(baseline.locked(root))  # before they start, so neither can save first
        with _cli(root, *(["baseline", "save", "main", run, "--expect-previous", A] for run in (B, C))) as procs:
            _wait_on_the_lock(root, 2)
            lock.close()
            results = {run: proc.communicate(timeout=60) + (proc.returncode,)
                       for run, proc in zip((B, C), procs, strict=True)}

    codes = sorted(code for _, _, code in results.values())
    assert codes == [0, 4], results
    winner = next(run for run, (_, _, code) in results.items() if code == 0)
    assert _pointer(root, "main") == winner + "\n"


@pytest.mark.skipif(not Path("/proc/locks").exists(), reason="waiting on a lock is seen in /proc/locks (Linux)")
def test_gc_keep_baselines_removes_while_it_holds_the_lock(root):
    """docs/runs.md, --keep-baselines: gc holds the baselines' lock while it removes, so it waits for
    a save that holds it, and a save waits for gc."""
    baseline.save(root, "main", A, None)
    with contextlib.ExitStack() as lock:
        lock.enter_context(baseline.locked(root))  # before gc starts, so it cannot take the lock first
        with _cli(root, ["gc", "--older-than", "0d", "--keep-baselines"]) as (gc,):
            _wait_on_the_lock(root, 1)
            assert (root / "b" / "arms.json").exists()  # nothing removed before gc has the lock
            lock.close()
            out, err = gc.communicate(timeout=60)

    assert gc.returncode == 0, err
    assert f"kept 1 run older than 0d that a baseline holds: {A} (main)" in out
    assert not (root / "b" / "arms.json").exists() and (root / "a" / "eval.json").exists()


@pytest.mark.parametrize("argv", [["--keep-baselines", "--dry-run"], []])
def test_gc_takes_no_lock_when_it_keeps_no_baseline_or_removes_nothing(root, monkeypatch, capsys, argv):
    def forbidden(path):
        raise AssertionError("gc took the baselines' lock")

    monkeypatch.setattr(baseline, "locked", forbidden)

    assert _run(monkeypatch, capsys, "gc", "--older-than", "0d", *argv)[0] == 0
    assert not (root / ".baselines").exists()


@pytest.mark.parametrize("name", ["../x", "a//b", "/a", "a/", ".hidden", "a/.b", "a.", "a./b",
                                  "a b", "a:b", "a\\b", "", "x" * 129, "con", "NUL.txt", "com1"])
def test_save_refuses_a_name_that_is_not_one_and_writes_nothing(root, monkeypatch, capsys, name):
    code, _, err = _run(monkeypatch, capsys, "baseline", "save", name, A)

    assert code == 2 and "is not a baseline name" in err
    assert not (root / ".baselines").exists()


@pytest.mark.parametrize("name", ["main", "skill/a@main", "A.b_c-d/e@f.1", "x" * 128,
                                  "/".join("ab" for _ in range(43)), "console", "con-x/y"])
def test_save_takes_a_name_of_segments(root, monkeypatch, capsys, name):
    assert _run(monkeypatch, capsys, "baseline", "save", name, A)[0] == 0
    assert [p.name for p in baseline.read_all(root).pointers] == [name]


@pytest.mark.skipif(not hasattr(os, "geteuid") or os.geteuid() == 0,
                    reason="root lists a directory it may not read; Windows has no such mode")
@pytest.mark.parametrize("argv", [["save", "Main", B], ["rm", "Main"]])
def test_a_store_that_cannot_be_listed_refuses_what_the_case_rule_needs(root, monkeypatch, capsys, argv):
    # docs/runs.md, Names: the case rule needs the store's names, so no listing is no save or rm
    _run(monkeypatch, capsys, "baseline", "save", "main", A)
    (root / ".baselines").chmod(0o333)
    try:
        code, _, err = _run(monkeypatch, capsys, "baseline", *argv)
    finally:
        (root / ".baselines").chmod(0o755)

    assert (code, err) == (2, "Error: results/.baselines/: cannot list: Permission denied\n")
    assert sorted(os.listdir(root / ".baselines")) == [".lock", "main"]


def test_a_name_that_differs_only_in_case_is_refused(root, monkeypatch, capsys):
    _run(monkeypatch, capsys, "baseline", "save", "skill/a@main", A)

    code, _, err = _run(monkeypatch, capsys, "baseline", "save", "Skill/A@main", B)

    assert code == 2
    assert err == ("Error: baseline Skill/A@main differs from baseline skill/a@main only in case, "
                   "which Windows and macOS read as one name\n")
    assert _pointer(root, "skill/a@main") == A + "\n"


@pytest.mark.parametrize("argv, message", [
    (["main", C], f"no artifact under the runs root results/ has the run_id {C}"),
    (["main", "not a run"], "RUN 'not a run' is not a run id"),
    (["main", A, "--expect-previous", ""], "--expect-previous '' is not a run id"),
])
def test_save_refuses_a_run_that_is_not_under_the_root(root, monkeypatch, capsys, argv, message):
    code, _, err = _run(monkeypatch, capsys, "baseline", "save", *argv)

    assert (code, err) == (2, f"Error: {message}\n")
    assert _pointer(root, "main") is None


def test_a_pointer_that_cannot_be_written_exits_5_and_keeps_the_old_one(root, monkeypatch, capsys):
    _run(monkeypatch, capsys, "baseline", "save", "main", A)

    def refuse(path, text):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(baseline, "write_text_atomic", refuse)
    code, _, err = _run(monkeypatch, capsys, "baseline", "save", "main", B)

    assert (code, err) == (5, "Error: baseline main: cannot write: Permission denied\n")
    assert _pointer(root, "main") == A + "\n"


def test_rm_of_a_baseline_that_does_not_exist_exits_2(root, monkeypatch, capsys):
    assert _run(monkeypatch, capsys, "baseline", "rm", "main")[:3:2] == (
        2, "Error: no baseline main under the runs root results/\n")
    _run(monkeypatch, capsys, "baseline", "save", "main", A)
    assert _run(monkeypatch, capsys, "baseline", "rm", "other")[0] == 2
    assert _pointer(root, "main") == A + "\n"


def test_rm_of_a_name_that_differs_only_in_case_removes_nothing(root, monkeypatch, capsys):
    # save refuses such a name (docs/runs.md, "Names"); rm must not remove main for MAIN, which a
    # case-insensitive file system (Windows, macOS) opens as one file: _read stands in for that here
    _run(monkeypatch, capsys, "baseline", "save", "main", A)
    monkeypatch.setattr(baseline, "_read", lambda path: A)

    code, _, err = _run(monkeypatch, capsys, "baseline", "rm", "MAIN")

    assert (code, err) == (2, "Error: no baseline MAIN under the runs root results/ (baseline main "
                              "differs only in case)\n")
    assert _pointer(root, "main") == A + "\n"


def test_without_a_runs_root_list_is_empty_and_save_and_rm_exit_2(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DOKIMI_RUNS_DIR", raising=False)

    assert _run(monkeypatch, capsys, "baseline", "list")[:2] == (0, "0 baselines under results/\n")
    for argv in (["save", "main", A], ["rm", "main"]):
        code, _, err = _run(monkeypatch, capsys, "baseline", *argv)
        assert (code, err) == (2, "Error: no runs root: results/ does not exist (DOKIMI_RUNS_DIR "
                                  "names another root)\n")
    assert not (tmp_path / "results").exists()


def test_list_names_a_store_entry_that_is_not_a_pointer(root, monkeypatch, capsys):
    _run(monkeypatch, capsys, "baseline", "save", "main", A)
    (root / ".baselines" / "broken").write_text("not a run id\n", encoding="utf-8")
    (root / ".baselines" / "a b").write_text(A, encoding="utf-8")

    code, out, _ = _run(monkeypatch, capsys, "baseline", "list")

    assert code == 0
    assert out.endswith("1 baseline under results/\nunreadable: .baselines/a b: not a baseline name\n"
                        "unreadable: .baselines/broken: holds no run id\n")
    doc = json.loads(_run(monkeypatch, capsys, "baseline", "list", "--json")[1])
    assert doc["status"] == "partial"
    assert [u["path"] for u in doc["data"]["unreadable"]] == [".baselines/a b", ".baselines/broken"]


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="a FIFO is a POSIX file")
def test_a_fifo_in_the_store_is_unreadable_and_never_waited_on(root, monkeypatch, capsys):
    # docs/runs.md, Baselines: an entry that is not a pointer is unreadable; opening a FIFO would block
    (root / ".baselines").mkdir()
    os.mkfifo(root / ".baselines" / "main")

    assert _run(monkeypatch, capsys, "baseline", "list")[1].endswith(
        "unreadable: .baselines/main: cannot read: not a regular file\n")
    assert _run(monkeypatch, capsys, "gc", "--older-than", "0d", "--keep-baselines")[::2] == (
        2, "Error: .baselines/main under the runs root: cannot read: not a regular file, so the run "
           "it holds is not known\n")
    assert _run(monkeypatch, capsys, "baseline", "save", "main", A)[::2] == (
        2, "Error: baseline main: cannot read: not a regular file\n")
    assert _run(monkeypatch, capsys, "baseline", "rm", "main")[:2] == (
        0, "removed baseline main (it held no run id); the run stays\n")
    assert not os.path.lexists(root / ".baselines" / "main")


@pytest.mark.parametrize("path", [".baselines/.lock", ".baselines/main", "results/.baselines/main",
                                  "{root}/.baselines/../.baselines/.lock"])
def test_runs_rm_refuses_a_file_of_the_baselines_store(root, monkeypatch, capsys, path):
    # docs/runs.md, runs rm: removing the lock while it is held would let a second save run beside the first
    _run(monkeypatch, capsys, "baseline", "save", "main", A)
    path = path.format(root=root)

    code, _, err = _run(monkeypatch, capsys, "rm", path)

    assert (code, err) == (2, f"Error: {path} is in the baselines' store .baselines/; runs baseline "
                              "rm NAME removes a baseline\n")
    assert sorted(os.listdir(root / ".baselines")) == [".lock", "main"]


def test_a_pointer_longer_than_a_run_id_line_is_not_read_as_its_first_bytes(root, monkeypatch, capsys):
    # docs/runs.md, On disk: the file holds one line, the run_id; padding hid what came after it
    (root / ".baselines").mkdir()
    (root / ".baselines" / "main").write_text(A + " " * 300 + "garbage\n", encoding="utf-8")
    unreadable = "cannot read: longer than a pointer (256 bytes)"

    assert _run(monkeypatch, capsys, "baseline", "list")[1].endswith(
        f"0 baselines under results/\nunreadable: .baselines/main: {unreadable}\n")
    assert _run(monkeypatch, capsys, "gc", "--older-than", "0d", "--keep-baselines")[0] == 2
    assert _run(monkeypatch, capsys, "baseline", "save", "main", B, "--expect-previous", A)[::2] == (
        2, f"Error: baseline main: {unreadable}\n")
    assert _pointer(root, "main").endswith("garbage\n")


@pytest.mark.skipif(sys.platform == "win32", reason="a symlink needs a privilege on Windows")
def test_rm_removes_a_pointer_that_is_a_link_to_nothing_so_gc_can_run(root, monkeypatch, capsys):
    # docs/runs.md: such an entry is unreadable and stops gc --keep-baselines; rm is how it goes
    (root / ".baselines").mkdir()
    (root / ".baselines" / "main").symlink_to(root / "gone")
    assert _run(monkeypatch, capsys, "gc", "--older-than", "0d", "--keep-baselines")[0] == 2

    assert _run(monkeypatch, capsys, "baseline", "rm", "main")[:2] == (
        0, "removed baseline main (it held no run id); the run stays\n")
    assert not os.path.lexists(root / ".baselines" / "main")
    assert _run(monkeypatch, capsys, "gc", "--older-than", "0d", "--keep-baselines")[0] == 0


@pytest.mark.skipif(sys.platform == "win32", reason="a symlink needs a privilege on Windows")
@pytest.mark.parametrize("path", [".baselines/.lock", "store/.lock", "store/main"])
def test_runs_rm_refuses_the_store_however_it_is_reached(root, monkeypatch, capsys, path):
    # a .baselines that links to a directory under the root resolves to that directory's name
    (root / "store").mkdir()
    (root / ".baselines").symlink_to(root / "store", target_is_directory=True)
    _run(monkeypatch, capsys, "baseline", "save", "main", A)

    assert _run(monkeypatch, capsys, "rm", path)[::2] == (
        2, f"Error: {path} is in the baselines' store .baselines/; runs baseline rm NAME removes a "
           "baseline\n")
    assert sorted(os.listdir(root / "store")) == [".lock", "main"]


def test_runs_list_does_not_walk_the_pointers(root, monkeypatch, capsys):
    _run(monkeypatch, capsys, "baseline", "save", "main", A)

    out = _run(monkeypatch, capsys, "list")[1]

    assert "skipped" not in out and ".baselines" not in out
    assert out.endswith("3 artifacts under results/ (* the file's mtime: the artifact records no "
                        "time)\nruns: 2 (3 artifacts)\n")


def test_gc_keep_baselines_keeps_the_run_a_pointer_holds(root, monkeypatch, capsys):
    _run(monkeypatch, capsys, "baseline", "save", "skill/a@main", A)

    code, out, _ = _run(monkeypatch, capsys, "gc", "--older-than", "0d", "--keep-baselines")

    assert code == 0
    assert out.endswith(f"kept 1 run older than 0d that a baseline holds: {A} (skill/a@main)\n")
    assert (root / "a" / "eval.json").exists() and not (root / "b" / "arms.json").exists()
    assert _pointer(root, "skill/a@main") == A + "\n"


LOCK_REFUSED = "Error: results/.baselines/: cannot lock: not a directory\n"


@pytest.mark.parametrize("argv, message", [
    (["gc", "--older-than", "0d", "--keep-baselines"], LOCK_REFUSED),
    (["baseline", "save", "main", A], LOCK_REFUSED),
    (["baseline", "rm", "main"], "Error: no baseline main under the runs root results/\n"),
])
def test_a_baselines_store_that_is_a_file_exits_2_and_changes_nothing(
        root, monkeypatch, capsys, argv, message):
    # docs/runs.md, Exit codes: a .baselines/ that cannot be locked or listed is an input error
    (root / ".baselines").write_text("", encoding="utf-8")

    assert _run(monkeypatch, capsys, *argv)[::2] == (2, message)
    assert (root / "a" / "eval.json").exists() and (root / "b" / "arms.json").exists()


@pytest.mark.skipif(not hasattr(os, "geteuid") or os.geteuid() == 0,
                    reason="root writes into a read-only directory; Windows has no such mode")
def test_gc_keep_baselines_in_a_store_where_no_lock_can_be_made_exits_2(root, monkeypatch, capsys):
    (root / ".baselines").mkdir(mode=0o555)
    try:
        code, _, err = _run(monkeypatch, capsys, "gc", "--older-than", "0d", "--keep-baselines")
    finally:
        (root / ".baselines").chmod(0o755)

    assert (code, err) == (2, "Error: results/.baselines/: cannot lock: Permission denied\n")
    assert (root / "b" / "arms.json").exists()


def test_gc_keep_baselines_removes_nothing_while_a_pointer_cannot_be_read(root, monkeypatch, capsys):
    (root / ".baselines").mkdir()
    (root / ".baselines" / "main").write_text("", encoding="utf-8")

    code, _, err = _run(monkeypatch, capsys, "gc", "--older-than", "0d", "--keep-baselines")

    assert code == 2
    assert err == ("Error: .baselines/main under the runs root: holds no run id, so the run it "
                   "holds is not known\n")
    assert (root / "a" / "eval.json").exists() and (root / "b" / "arms.json").exists()
