<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

# Adding a static documentation metric

The static benchmark scores every Markdown file in the configured root with a
small set of pluggable metrics. Each metric is a pure Python module under
`dokimi/metrics/` that exposes a `score(text: str) -> float`
function returning a value in `[0, 1]`, plus an entry in the spec.

This guide shows how to add a new one end-to-end.

## 1 — Write the metric module

```python
# dokimi/metrics/link_validity.py
# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
"""Fraction of intra-doc links that resolve to a known anchor."""

from __future__ import annotations

import re

LINK_RE = re.compile(r"\[[^\]]+\]\(([^)]+)\)")


def score(text: str, *, known_anchors: set[str] | None = None) -> float:
    links = LINK_RE.findall(text)
    if not links:
        return 1.0
    if known_anchors is None:
        return 1.0
    good = sum(1 for href in links if href.lstrip("#") in known_anchors)
    return round(good / len(links), 4)
```

Conventions:

- Pure function. No I/O, no logging, no globals beyond compiled regexes.
- Return `0.0` for empty input and `1.0` when the metric "doesn't apply"
  (e.g., no links to check), or document the chosen sentinel clearly.
- Round to 4 decimals so JSON diffs are readable.
- Keep optional config (thresholds, `max_age_days`, …) as keyword arguments
  with sensible defaults — the runner pulls them from the spec.

For reference, see the existing metrics:

- `dokimi/metrics/coverage.py` — heading / code-block / body
  heuristic.
- `dokimi/metrics/freshness_lite.py` — modification-age decay.
- `dokimi/metrics/readability.py` — Flesch-Kincaid-style score.
- `dokimi/metrics/example_runner.py` — executes fenced code blocks.

## 2 — Wire it into the runner

`dokimi/runner/run.py` imports each metric module and writes its
score into the per-document bundle. Add your module to the imports and to
the per-file scoring loop, and extend the `active_metrics` list when the
metric is enabled in the spec.

## 3 — Add it to the spec

`benchmarks/spec.v1.yaml`:

```yaml
weights:
  coverage: 0.30
  freshness_lite: 0.20
  readability: 0.15
  example_pass_rate: 0.20
  link_validity: 0.15        # ← new

metrics:
  link_validity:
    enabled: true
    weight: 0.15
    target: 0.95
```

Re-normalize the other weights so they sum to 1.0 over the enabled set, or
let the runner do it (it normalizes across active metrics at score time).

Update `dokimi/runner/spec.schema.json` to declare any new config keys your
metric reads — the runner validates the spec against the schema and will
refuse to start if required keys are missing.

## 4 — Tests

Add a focused unit test under `tests/`:

```python
# tests/test_link_validity.py
# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
from dokimi.metrics import link_validity


def test_empty_text_scores_one():
    assert link_validity.score("") == 1.0


def test_unresolved_link_drops_score():
    text = "See [foo](#missing) and [bar](#known)."
    assert link_validity.score(text, known_anchors={"known"}) == 0.5
```

Then add an integration assertion to `tests/test_runner_run.py` (or equivalent)
covering the metric flowing through `cli.py run`.

## 5 — Document it

Update the metric table in [`README.md`](../README.md#static-benchmark-metrics).
If your metric introduces a new gate type or comparison semantic, mention
it in [`architecture.md`](architecture.md).

## 6 — Run the benchmark and check the snapshot

```bash
python cli.py run \
  --root . \
  --spec benchmarks/spec.v1.yaml \
  --out-json baselines/current.json \
  --out-md reports/current.md

jq '.documents[0].metrics' baselines/current.json
```

The new metric should appear with a score in `[0, 1]` and contribute to the
weighted total. The compare command picks it up automatically, against a base
snapshot you took before the change (there is no committed baseline — see
[`benchmarking-and-comparison.md`](benchmarking-and-comparison.md#static-documentation-benchmark)):

```bash
python cli.py compare \
  --base baselines/baseline.json \
  --candidate baselines/current.json \
  --spec benchmarks/spec.v1.yaml \
  --out-md reports/compare.md
```
