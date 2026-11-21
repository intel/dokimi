# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Run the test files a PR adds or changes several times under varied pytest-randomly seeds.

A test that passes once and fails on a reordering or under xdist load is cheapest to catch
before it lands. CI's ``Repeat changed tests`` step (ci.yml, ``test`` job, pull requests only)
runs:

    python scripts/repeat_changed_tests.py --base origin/main --head HEAD

Each ``dokimi/tests/test_*.py`` that ``base...head`` adds, modifies or renames runs ``--repeats``
times (default 20) with ``pytest -n auto --randomly-seed=1 ... N``. At most ``--max-files``
(default 10) files are repeated, in name order; the rest are named in the log and run only once,
in the full suite. Exits 0 when no test file changed, 1 on the first failing seed (its number is
in the log: ``pytest --randomly-seed=<seed> <files>`` replays it), 2 when git cannot diff or
``--repeats`` or ``--max-files`` is not a positive integer.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PROJECT = REPO / "dokimi"
TEST_FILE = re.compile(r"^dokimi/tests/(test_[^/]+\.py)$")


def changed_test_files(base: str, head: str, repo: Path = REPO) -> list[str]:
    """Names (relative to ``dokimi/``) of the test files added, modified or renamed in ``base...head``."""
    done = subprocess.run(
        ["git", "diff", "-z", "--name-only", "--diff-filter=AMR", f"{base}...{head}"],
        cwd=repo, capture_output=True, check=True,
    )
    paths = (os.fsdecode(raw) for raw in done.stdout.split(b"\0") if raw)
    return sorted(f"tests/{m.group(1)}" for m in map(TEST_FILE.match, paths) if m)


def _pytest(files: Sequence[str], seed: int) -> int:
    cmd = [sys.executable, "-m", "pytest", "-n", "auto", "-q", "-p", "no:cacheprovider",
           f"--randomly-seed={seed}", *files]
    return subprocess.run(cmd, cwd=PROJECT, check=False).returncode


def _shown(names: Sequence[str]) -> str:
    """*names* for a log line: bytes that are not UTF-8 print as ``\\xe9`` instead of raising."""
    return " ".join(os.fsencode(n).decode("utf-8", errors="backslashreplace") for n in names)


def repeat(files: Sequence[str], repeats: int, max_files: int,
           runner: Callable[[Sequence[str], int], int] = _pytest) -> int:
    """0 when every seed passes; 1 at the first failing seed; 0 with a log line when *files* is empty."""
    if not files:
        print("No test file added or changed: nothing to repeat.")
        return 0
    chosen, left_out = list(files[:max_files]), list(files[max_files:])
    print(f"Repeating {len(chosen)} test file(s) under {repeats} seeds: {_shown(chosen)}")
    if left_out:
        print(f"{len(left_out)} more changed test file(s) over the cap of {max_files} are not "
              f"repeated (they run once in the full suite): {_shown(left_out)}")
    for seed in range(1, repeats + 1):
        print(f"::group::--randomly-seed={seed}")
        code = runner(chosen, seed)
        print("::endgroup::")
        if code:
            print(f"::error::pytest exited {code} under --randomly-seed={seed}: replay with "
                  f"`pytest -n auto --randomly-seed={seed} {_shown(chosen)}`", file=sys.stderr)
            return 1
    print(f"All {repeats} seeds passed.")
    return 0


def _positive(text: str) -> int:
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{text!r} is not an integer") from None
    if value < 1:
        raise argparse.ArgumentTypeError(f"{text} is not a positive integer")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base", default="origin/main")
    parser.add_argument("--head", default="HEAD")
    parser.add_argument("--repeats", type=_positive, default=20)
    parser.add_argument("--max-files", type=_positive, default=10)
    args = parser.parse_args(argv)
    try:
        files = changed_test_files(args.base, args.head)
    except subprocess.CalledProcessError as exc:
        print(f"cannot diff {args.base}...{args.head}: {(exc.stderr or b'').decode(errors='replace').strip()}",
              file=sys.stderr)
        return 2
    return repeat(files, args.repeats, args.max_files)


if __name__ == "__main__":
    raise SystemExit(main())
