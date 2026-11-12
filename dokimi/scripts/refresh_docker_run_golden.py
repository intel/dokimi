# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Re-take the docker-run argv golden from the package's own argv builders.

Replays the grid of ``tests/test_docker_run_argv_differential.py`` (every ``docker run`` the package
builds, with and without a sandbox, every network, cpus and memory spelling) and writes
``tests/fixtures/docker/run_argv_golden.json``. No docker is started.

    python3 scripts/refresh_docker_run_golden.py             # rewrite the golden
    python3 scripts/refresh_docker_run_golden.py --out F     # write elsewhere, to compare

A diff of the golden is an argv change, a behaviour change of the docker command: read it before
committing. A planned change to the flags is the reason to run this; a refactor must leave the file
byte-identical (``git diff --exit-code``).
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "tests" / "fixtures" / "docker" / "run_argv_golden.json"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))  # the grid and its fake docker CLI live with the test

import test_docker_run_argv_differential as grid  # noqa: E402


def render(out: dict) -> str:
    """The golden's text: indented JSON with sorted keys, one trailing newline."""
    return json.dumps(out, indent=1, sort_keys=True) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=GOLDEN, help="golden file to write (default: the golden)")
    args = ap.parse_args(argv)
    with tempfile.TemporaryDirectory(prefix="docker-argv-grid-") as scratch:
        text = render(grid.capture(Path(scratch)))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(text, encoding="utf-8")
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
