<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Added

- **`dokimi_config.v1`, the repository config schema and loader (library only, no CLI yet)** — a
  repository's `.github/dokimi/config.yaml` (or `.dokimi/config.yaml`) declares its subjects (skills, and
  the repository itself with `kind: repo`), where each subject's evals are (beside the skill by default,
  or a central glob such as `evaluation/harbor/tasks/onetbb-*`), the plan defaults, `shared` paths for
  the smoke set, and the budget and baseline store. The loader `dokimi.repo_config` finds the file in
  the order explicit path, `.github/dokimi/config.yaml`, `.dokimi/config.yaml` under the repository root,
  validates it against the schema, and reports each subject's `skill_sha` (the tree digest of the skill
  directory without its evals and without any nested skill directory, which is its own subject) and
  `suite_hash`. A skill's suite claims whole directories of its skill (its evals, or a directory such as
  `tests/**`), and those directories leave `skill_sha`. A suite that picks an individual file of its skill
  (`SKILL.md`, a script, a reference, one eval file) is refused at load, naming the subject and the file. `baseline.store: false`
  reads as `off`, and a budget number that YAML reads as text (`1e3`) is refused with the plain
  decimal or the `1.0e+3` spelling, and a `.nan` or `.inf` budget is refused. A suite directory is
  hashed file by file, following symlinks, so an edit to a shared eval moves `suite_hash`. Nothing reads the file yet: the commands that use it come later,
  and existing `.agent-eval/` lookups are unchanged.
