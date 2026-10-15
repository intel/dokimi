#!/usr/bin/env python3
"""schema-required-field: explore_runs task entries carry a required ``digest``.

Run as ``python check.py <run_dir> <task_dir>`` with cwd = <workspace>/dokimi. Prints one
JSON object of metrics as the last stdout line. Never raises.

new_version_registered  SCHEMA_VERSIONS["explore_runs"] is a later explore_runs.vN whose file
                        exists and whose const is that version
old_superseded          explore_runs.v1 is listed in SUPERSEDED_VERSIONS and its file is kept
old_loads               the start commit's explore_runs.v1 fixture still validates on load
writer_emits_digest     `explore run` writes a non-empty digest per task entry: equal across
                        two processes for the same YAML, different after the references or
                        the query change; the artifact is stamped with the current version
missing_rejected        that artifact, minus one task's digest, fails validation
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path

PROJ = Path.cwd()
WS = PROJ.parent
sys.path.insert(0, str(PROJ))
metrics: dict = {}
state: dict = {}


def note(*a):
    print(*a, flush=True)


def run(name, fn):
    try:
        metrics[name] = bool(fn())
    except Exception:
        note(f"[{name}] exception:\n{traceback.format_exc()[-2000:]}")
        metrics[name] = False
    note(f"{name} = {metrics[name]}")


def artifacts():
    import dokimi.artifacts as a
    return a


def new_version_registered():
    a = artifacts()
    cur = a.SCHEMA_VERSIONS.get("explore_runs", "")
    m = re.fullmatch(r"explore_runs\.v(\d+)", cur)
    note("current explore_runs version:", cur)
    if not m or int(m.group(1)) < 2:
        return False
    path = a.SCHEMA_DIR / f"{cur}.json"
    if not path.exists():
        return False
    schema = json.loads(path.read_text(encoding="utf-8"))
    return schema.get("properties", {}).get("schema_version", {}).get("const") == cur


def old_superseded():
    a = artifacts()
    sup = a.SUPERSEDED_VERSIONS.get("explore_runs", ())
    note("superseded:", sup)
    return "explore_runs.v1" in sup and (a.SCHEMA_DIR / "explore_runs.v1.json").exists()


def old_loads(start):
    a = artifacts()
    raw = subprocess.run(["git", "-C", str(WS), "show",
                          f"{start}:dokimi/tests/fixtures/artifacts/explore_runs.v1.json"],
                         capture_output=True, text=True, check=True).stdout
    data = json.loads(raw)
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "old.json"
        p.write_text(json.dumps(data), encoding="utf-8")
        a.load_artifact("explore_runs", p)
    return True


TASK_YAML = """id: find_scorer
product: demo
query: {query}
references:
  - path: src/score.py
    lines: [{start}, 40]
  - path: docs/scoring.md
"""


def explore_run(tmp, tag):
    out = tmp / f"{tag}.json"
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    p = subprocess.run(
        [sys.executable, str(PROJ / "cli.py"), "explore", "run", "--all",
         "--tasks-root", str(tmp / "tasks"), "--repo-root", str(tmp / "repo"),
         "--arms", "oracle,empty", "--baseline-arm", "empty",
         "--output-dir", str(tmp / "out" / tag), "--out-json", str(out)],
        cwd=tmp, capture_output=True, text=True, timeout=300, env=env)
    note(f"explore run {tag}: rc={p.returncode} {p.stdout[-300:]} {p.stderr[-1500:]}")
    if p.returncode:
        return None
    return json.loads(out.read_text(encoding="utf-8"))


def digests(art):
    return [t.get("digest") for t in art.get("tasks", [])] if art else [None]


def writer_emits_digest(tmp):
    a = artifacts()
    (tmp / "tasks").mkdir(parents=True)
    (tmp / "repo" / "src").mkdir(parents=True)
    (tmp / "repo" / "docs").mkdir(parents=True)
    (tmp / "repo" / "src" / "score.py").write_text("\n".join(f"x{i} = {i}" for i in range(60)))
    (tmp / "repo" / "docs" / "scoring.md").write_text("# Scoring\n")
    y = tmp / "tasks" / "find_scorer.yaml"
    q = "Where is the localization score computed?"

    y.write_text(TASK_YAML.format(query=q, start=10), encoding="utf-8")
    r1 = explore_run(tmp, "r1")
    r2 = explore_run(tmp, "r2")
    y.write_text(TASK_YAML.format(query=q, start=12), encoding="utf-8")
    r3 = explore_run(tmp, "r3")
    y.write_text(TASK_YAML.format(query=q + " And where is it tested?", start=10), encoding="utf-8")
    r4 = explore_run(tmp, "r4")
    d1, d2, d3, d4 = (digests(r)[0] if r else None for r in (r1, r2, r3, r4))
    note("digests:", d1, d2, d3, d4)
    state["artifact"] = r1
    if not all(isinstance(d, str) and d for d in (d1, d2, d3, d4)):
        return False
    stamped = r1.get("schema_version") == a.SCHEMA_VERSIONS["explore_runs"]
    note("stamped with current version:", stamped, r1.get("schema_version"))
    return stamped and d1 == d2 and d3 != d1 and d4 != d1 and d4 != d3


def missing_rejected():
    a = artifacts()
    art = state.get("artifact")
    if not art or not art.get("tasks"):
        return False
    a.validate_artifact("explore_runs", art)  # complete artifact is valid
    broken = json.loads(json.dumps(art))
    broken["tasks"][0].pop("digest", None)
    try:
        a.validate_artifact("explore_runs", broken)
    except a.ArtifactValidationError as exc:
        note("missing digest rejected:", str(exc)[:300])
        return True
    note("an artifact without a task digest validated")
    return False


def main():
    rd = Path(sys.argv[1])
    try:
        start = json.loads((rd / "meta.json").read_text())["start_sha"]
    except Exception:
        start = "HEAD"
    run("new_version_registered", new_version_registered)
    run("old_superseded", old_superseded)
    run("old_loads", lambda: old_loads(start))
    with tempfile.TemporaryDirectory(prefix="srf-check-") as d:
        run("writer_emits_digest", lambda: writer_emits_digest(Path(d)))
    run("missing_rejected", missing_rejected)
    print(json.dumps(metrics))


if __name__ == "__main__":
    main()
