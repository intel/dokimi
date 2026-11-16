<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

# skill_evidence.v1 conformance fixtures

Each file is a valid `skill_evidence.v1` document with one result. A consumer passes when:

| File | Expectation |
|---|---|
| `verdict_without_rule.json` | The result has a `verdict` but no `verdict_rule`: mark it non-comparable. |
| `verdict_with_rule.json` | Recomputing the verdict from `delta`, `p_value`, `verdict_rule` and `metric_spec` gives the stated `improved`. |
| `judge_snapshot_older.json`, `judge_snapshot_newer.json` | The same protocol resolved against judge snapshots `…-20260901` and `…-20261101`: the `protocol.digest` values differ, and the newer result is non-comparable to the older one. |

`protocol.digest` is `sha256:` + the hex sha256 of `protocol.resolved` as JSON with sorted keys, no whitespace, UTF-8 and no NaN. `tests/test_evidence_protocol.py` runs these checks against `dokimi.subjects.evidence_check`.
