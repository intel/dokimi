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
  `suite_hash`. In a git work tree both hash each file's git blob id, so ignored paths, a nested `.git` and a CRLF
  checkout (`core.autocrlf`) leave them as they are, and a committed line-ending change moves them; outside a
  work tree they hash the bytes on disk. A skill's suite claims whole directories of its skill (its evals, or a directory such as
  `tests/**`), and those directories leave `skill_sha`. A suite may pick individual files under its skill's
  `evals` directory; those files leave `skill_sha` and move `suite_hash`. A suite that picks an individual file
  elsewhere in its skill (`SKILL.md`, a script, a reference) is refused at load, naming the subject and the file. A link in a suite that resolves into its skill is refused too. An explicit subject whose id is taken by a default skill at another directory is refused instead of silently replacing it. `baseline.store: false`
  reads as `off`, a `branch:` store name with whitespace, a leading `-` or a final newline is refused, and a budget number that YAML reads as text (`1e3`) is refused with the plain
  decimal or the `1.0e+3` spelling, and a `.nan` or `.inf` budget is refused. YAML anchors and aliases are refused. A suite directory is
  hashed file by file. In v1 a link is refused in a suite and on the way to one (a skill may hold only its own
  directory as a link, when that links inside the repository; a repository's manifest may be reached through links),
  so its retargeting is never attributed to the suite. Nothing reads the file yet: the commands that use it come later,
  and existing `.agent-eval/` lookups are unchanged.

### Changed

- **An absolute link inside a build context digests by a relative target** — a pinned batch or `worktree-` image tag for such a context changes once.
