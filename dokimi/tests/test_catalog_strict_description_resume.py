# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""#1089: ``catalog check --strict --resume`` keeps failing on a reused ``description-too-long`` row when
``--ignore listing-over-budget`` drops the listing overage."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
IGNORE = "listing-over-budget"


def _scan(root: Path, receipt: Path, *flags: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "cli.py", "catalog", "check", "--root", str(root), "--kind", "skills", "--per-skill",
         "--jsonl", str(receipt), "--listing-budget", "1", "--strict", *flags],
        cwd=PROJECT, capture_output=True, text=True, check=False, timeout=60,
    )


def _write(root: Path, name: str, description: str) -> None:
    path = root / name / "SKILL.md"
    path.parent.mkdir(parents=True)
    path.write_text(f"---\nname: {name}\ndescription: {description}\n---\n# Local records\n", encoding="utf-8")


def test_strict_resume_keeps_description_error_with_listing_overage_ignored(tmp_path):
    """#1089: a reused `description-too-long` row still exits 1 under `--strict` when the overage is ignored."""
    root, receipt = tmp_path / "catalog", tmp_path / "rows.jsonl"
    _write(root, "short", "Use when checking local records.")
    for failures in (0, 1):
        if failures:
            _write(root, "long", "Use when checking " + "local records " * 90 + "for accuracy.")
            unignored = _scan(root, receipt)
            assert unignored.returncode == 1 and f"warn {IGNORE}:" in unignored.stdout
        for resumed in (False, True):
            before = receipt.read_bytes() if resumed else None
            run = _scan(root, receipt, "--ignore", IGNORE, *(["--resume"] if resumed else []))
            assert run.returncode == failures, (failures, resumed, run.stdout, run.stderr)
            assert IGNORE not in run.stdout + run.stderr
            assert ("resumed from" in run.stdout) is resumed
            if resumed:
                assert receipt.read_bytes() == before
            rows = [json.loads(line) for line in receipt.read_text(encoding="utf-8").splitlines()]
            states = {r["name"]: (r["state"], [f["code"] for f in r["findings"]]) for r in rows}
            wanted = {"short": ("ok", [])}
            if failures:
                wanted["long"] = ("error", ["description-too-long"])
            assert states == wanted
