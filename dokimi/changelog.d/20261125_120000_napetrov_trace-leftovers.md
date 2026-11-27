<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Changed

- **`dokimi:typecheck` pins mypy in `mypy_version`, and `version` is the task's revision again.** A repository task that revises its checks no longer collides with the pin: the grader reads `mypy_version`, and falls back to `version` only for a task that has no `mypy_version`, with a deprecation note on the grade's stderr. Existing tasks keep grading unchanged. `eval check` names `mypy_version` when a task has neither. See `docs/repo-evaluation-contract.md` §3 and §4.5.
- **`repo run --label-host` has a manifest equivalent.** A `repo-eval.yaml` with `label_host: true` runs a task the host misses and records the miss, as `--label-host` does. `--no-label-host` turns it off for one run; the flag is unset by default, so an existing manifest keeps its behaviour. See `docs/repo-evaluation-contract.md` §2 and §4.3.

### Fixed

- **A question's `version` and `provenance` survive a trigger set round trip.** A trigger set built from a question set, and read back from a `questions` trigger set, keeps each question's `version` and `provenance` as written (a malformed value too) instead of dropping them.
- **`questions.v2` documents the `version` shape the parser checks**: a non-empty string or an integer, with a float such as `1.0` malformed (it loads with a warning, as before).
