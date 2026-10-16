#!/usr/bin/env python3
"""Checks for feat-static-metric: a `code_block_lang` static documentation metric.

Runs with cwd = <workspace>/dokimi. Prints one JSON object of metrics on its last line
(diagnostics go to stdout before it: the grader reads the last line of stdout+stderr).

- module_ok:      dokimi.metrics.code_block_lang.score(text) gives the expected share on
                  small inputs (value in [0, 1], the scale every other static metric uses).
- spec_wired:     benchmarks/spec.v1.yaml declares code_block_lang under weights and
                  metrics, enabled.
- run_per_doc:    `cli.py run` on a fixture docs dir, with the agent's spec (manifest bounds
                  relaxed, example execution off), writes the expected per-doc value.
- run_summary:    the snapshot summary carries the metric (mean over docs, or pooled share).
- readme_listed:  the README static-metrics table names it.
- test_refs:      a test file in the agent's tree mentions it.
Informational: compare_diff (cli.py compare diffs it), arch_listed (architecture.md list),
report_md (markdown run report mentions it).
"""

from __future__ import annotations

import copy
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

PROJ = Path.cwd()
sys.path.insert(0, str(PROJ))
M = "code_block_lang"
TOL = 1.5e-3

DOC_A = """# Alpha

Some prose about the tool.

```bash
ls -la
```

```python
print("hi")
```

```
plain block
```

```yaml
key: value
```
"""  # 3 of 4 labelled -> 0.75

DOC_B = """# Beta

Text.

```
one
```

```console
$ echo two
```

```
three
```
"""  # 1 of 3 labelled -> 0.3333

EXPECT = {"docs/a.md": 0.75, "docs/b.md": 1 / 3}
SUMMARY_OK = ((0.75 + 1 / 3) / 2, 4 / 7)


def close(v, target):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and abs(float(v) - target) <= TOL


def metric_of(d):
    if not isinstance(d, dict):
        return None
    if M in d:
        return d[M]
    for k in ("metrics", "scores"):
        if isinstance(d.get(k), dict) and M in d[k]:
            return d[k][M]
    return None


def check_module():
    try:
        import importlib

        mod = importlib.import_module(f"dokimi.metrics.{M}")
        f = mod.score
        cases = [(DOC_A, 0.75), (DOC_B, 1 / 3),
                 ("```sh\nx\n```\n", 1.0), ("```\nx\n```\n\n```\ny\n```\n", 0.0)]
        return all(close(f(t), want) for t, want in cases)
    except Exception as exc:  # noqa: BLE001
        print(f"module: {exc!r}")
        return False


def load_spec():
    import yaml

    return yaml.safe_load((PROJ / "benchmarks" / "spec.v1.yaml").read_text(encoding="utf-8"))


def check_spec(spec):
    try:
        w = spec.get("weights", {})
        m = spec.get("metrics", {}).get(M)
        return M in w and isinstance(m, dict) and bool(m.get("enabled", False))
    except Exception:  # noqa: BLE001
        return False


def run_fixture(spec, tmp: Path):
    out = {"run_per_doc": False, "run_summary": False, "report_md": False, "compare_diff": None}
    import yaml

    root = tmp / "root"
    (root / "docs").mkdir(parents=True)
    (root / "docs" / "a.md").write_text(DOC_A, encoding="utf-8")
    (root / "docs" / "b.md").write_text(DOC_B, encoding="utf-8")
    s = copy.deepcopy(spec)
    s["golden_manifest"] = {"min_docs": 1, "max_docs": 100, "include": ["docs/**/*.md"], "exclude": []}
    ex = s.get("metrics", {}).get("example_pass_rate")
    if isinstance(ex, dict):
        ex["enabled"] = False  # never execute fixture code blocks
    sp = tmp / "spec.yaml"
    sp.write_text(yaml.safe_dump(s, sort_keys=False), encoding="utf-8")
    snaps = []
    for i in range(2):
        js, md = tmp / f"snap{i}.json", tmp / f"run{i}.md"
        p = subprocess.run([sys.executable, "cli.py", "run", "--root", str(root), "--spec", str(sp),
                            "--out-json", str(js), "--out-md", str(md)],
                           cwd=PROJ, capture_output=True, text=True, timeout=300)
        if p.returncode:
            print(f"cli run rc={p.returncode}: {(p.stdout + p.stderr)[-1500:]}")
            return out
        snaps.append(js)
    snap = json.loads(snaps[0].read_text())
    docs = {d.get("path"): d for d in snap.get("docs", [])}
    out["run_per_doc"] = all(close(metric_of(docs.get(k)), v) for k, v in EXPECT.items())
    sv = metric_of(snap.get("summary", {}))
    out["run_summary"] = any(close(sv, t) for t in SUMMARY_OK)
    out["report_md"] = M in (tmp / "run0.md").read_text(encoding="utf-8")
    # compare: same snapshot twice -> diff has the metric (zero delta). Informational.
    p = subprocess.run([sys.executable, "cli.py", "compare", "--base", str(snaps[0]),
                        "--candidate", str(snaps[1]), "--out-json", str(tmp / "cmp.json"),
                        "--out-md", str(tmp / "cmp.md")], cwd=PROJ, capture_output=True, text=True, timeout=300)
    if p.returncode == 0:
        out["compare_diff"] = M in json.loads((tmp / "cmp.json").read_text()).get("diff", {})
    else:
        out["compare_diff"] = False
    return out


def readme_listed():
    try:
        text = (PROJ / "README.md").read_text(encoding="utf-8")
        m = re.search(r"^#+\s*Static benchmark metrics\s*$(.*?)(?=^#{1,2}\s)", text, re.M | re.S | re.I)
        section = m.group(1) if m else ""
        return f"`{M}`" in section or re.search(rf"^\|.*\b{M}\b.*\|", section, re.M) is not None
    except Exception:  # noqa: BLE001
        return False


def arch_listed():
    try:
        return M in (PROJ / "docs" / "architecture.md").read_text(encoding="utf-8")
    except Exception:  # noqa: BLE001
        return False


def test_refs():
    for p in (PROJ / "tests").rglob("*.py"):
        if p.name.startswith("test_hidden_"):
            continue
        if M in p.read_text(encoding="utf-8", errors="replace"):
            return True
    return False


def main():
    res = {"module_ok": check_module(), "spec_wired": False, "run_per_doc": False, "run_summary": False,
           "readme_listed": readme_listed(), "test_refs": test_refs(), "arch_listed": arch_listed(),
           "report_md": False, "compare_diff": None}
    try:
        spec = load_spec()
        res["spec_wired"] = check_spec(spec)
        with tempfile.TemporaryDirectory() as td:
            res.update(run_fixture(spec, Path(td)))
    except Exception as exc:  # noqa: BLE001
        print(f"fixture: {exc!r}")
    print(json.dumps(res))


if __name__ == "__main__":
    os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    main()
