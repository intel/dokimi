# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Re-take the fidelity golden: what the pinned Claude Code sends for the docker-claude arms.

Runs each golden case (``bare``, ``skill``) through the arm's own argv builder against a
recording server, in a clean container by default, and writes the normalized request to
``tests/fixtures/fidelity/claude-code-<version>/<case>.json``. No model is called.

    python3 scripts/refresh_fidelity_golden.py                 # clean container, pinned version
    python3 scripts/refresh_fidelity_golden.py --host          # this host (not a golden source)
    python3 scripts/refresh_fidelity_golden.py --binary PATH   # another local build (check the version)

Read tests/fixtures/fidelity/README.md before committing a refreshed golden.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dokimi.harnesses.fidelity import (  # noqa: E402
    CASES, PINNED_CLAUDE_VERSION, capture_arm, residue, write_golden,
)

DEFAULT_IMAGE = "debian:trixie-slim"


def _version(binary: str) -> str:
    out = subprocess.run([binary, "--version"], capture_output=True, text=True, timeout=60, check=False)
    return (out.stdout or out.stderr).strip().split(" ")[0]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--binary", default=shutil.which("claude") or "claude")
    ap.add_argument("--image", default=DEFAULT_IMAGE, help="container image to run the client in")
    ap.add_argument("--host", action="store_true", help="run on this host instead of a container")
    ap.add_argument("--out", type=Path, default=None, help="golden directory (default: by version)")
    args = ap.parse_args(argv)

    binary = os.path.realpath(args.binary)
    version = _version(binary)
    if version != PINNED_CLAUDE_VERSION and not args.out:
        print(f"refusing: {binary} is Claude Code {version}, the golden pins {PINNED_CLAUDE_VERSION}; "
              f"pass --out to take a golden for another version", file=sys.stderr)
        return 2
    out = args.out or ROOT / "tests" / "fixtures" / "fidelity" / f"claude-code-{version}"
    image = None if args.host else args.image
    for case in CASES:
        scratch = Path(tempfile.mkdtemp(prefix=f"fidelity-{case}-"))
        try:
            request = capture_arm(case, binary=binary, scratch=scratch, image=image,
                                  uid=os.getuid(), gid=os.getgid())
        finally:
            shutil.rmtree(scratch, ignore_errors=True)
        dirty = residue(request)
        if dirty:
            print(f"{case}: residue left after normalization: {dirty[:5]}", file=sys.stderr)
            return 1
        write_golden(out / f"{case}.json", request, version=version, case=case)
        print(f"wrote {out / (case + '.json')} ({len(request.get('tools', []))} tools)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
