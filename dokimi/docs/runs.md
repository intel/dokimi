---
depends_on:
  - dokimi/dokimi/runs/**
  - dokimi/dokimi/commands/runs.py
---
<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

# The runs root: `runs list` and `runs show`

Most commands that run an evaluation write their artifacts under `results/` by
default (a few write elsewhere, listed below). `runs list` shows what is there
and `runs show` opens one artifact. Both only read files: no model call, no
network access, nothing written.

The runs root is `results/` in the working directory. Set `DOKIMI_RUNS_DIR` to
read another directory.

## `runs list`

```text
$ python cli.py runs list
SCHEMA           SUBJECT                     MODEL              TIME (UTC)         PATH
arms.v1          onetbb                      gpt-6.1-sol        2026-10-29 14:02   arms/onetbb.json
plugin_delta.v1  onetbb                      gpt-6.1-sol        2026-10-28 10:11*  arms/plugin_delta.json
eval.v1          -                           claude-sonnet-5-5  2026-10-28 10:05   onetbb_r7/eval/onetbb.json
answers.v1       onetbb                      gpt-6.1-sol        2026-10-28 09:40   onetbb_r7/answers/onetbb.json
novelty.v1       skill:skills/onetbb-tuning  claude-sonnet-5-5  2026-10-27 18:30   novelty/skill_skills_onetbb-tuning/novelty.json

5 artifacts under results/ (* the file's mtime: the artifact records no time)
skipped: 1 JSON with no leading schema_version, 1 JSON of an unknown schema_version (pairwise.v0: 1), 1 not .json
```

- **Each row is one artifact file.** A file is an artifact when it opens with the
  `schema_version` of a registered kind: `save_artifact` has written it as the
  first key since [#27](https://github.com/intel/dokimi/pull/27). Superseded
  versions such as `questions.v1` count too. Files that open with anything
  else are skipped without being read in full, so a large file of another kind
  costs nothing.
- **Subject, model and time** come from the artifact's own fields, and which
  fields depends on the kind: `product` of a questions set, `library_name` of
  an arms run, `artifact.ref` of a novelty run, `repo.id` of a reach run,
  `judge_model` and `evaluated_at` of an eval. An artifact that records no
  subject shows `-` (an eval, a task run, a questions set from `questions
  generate`, which records the model but not the product); nothing is guessed
  from the path. Rows are newest first. When an artifact records no time, its
  file's mtime is used for the order and shown with `*`.
- **Nothing is dropped silently.** The summary counts the files skipped, by
  reason: JSON that does not open with a `schema_version` (other output, and
  artifacts saved before #27, which did not put it first), JSON of an unknown
  `schema_version` (named), `*.tmp` and `*.rejected` files left by
  `save_artifact`, other files, entries that are not regular files (a FIFO),
  and symlinked directories, which are not walked. A file or directory that
  cannot be read, or a file that does not parse, is listed as unreadable, with
  the first one's reason.
- `--kind KIND` (repeatable, for example `--kind arms --kind eval`) and
  `--subject NAME` (an exact, case-sensitive match) filter the rows.
- `--json` prints `root`, `artifacts`, `total` (before filtering), `skipped`,
  `unknown_versions` and `unreadable`. Each artifact has `schema_version`,
  `kind`, `run_id`, `subject`, `model`, `generated_at` (the time the artifact
  records, `evaluated_at` for an eval; `null` when none), `path` (relative to
  the root) and `size_bytes`.

**What it does not show yet.** No artifact records a `run_id` yet
([#277](https://github.com/intel/dokimi/issues/277)), so rows are not grouped
into runs and `run_id` is `null`. Files outside the root are not listed:

- `repo run` writes its batches to `repo-runs/`, and its files carry no
  `schema_version`;
- `answers generate` and `eval score` write to `answers/` and `eval/` unless
  `--run-id` is given, and `eval panel-score` writes to `eval/`;
- `questions generate` and `questions refine` write to `questions/`,
  `repo reach` to `reach-out/`, `tools preference` to `tool_preference.json`
  in the working directory;
- an output flag can point anywhere.

## `runs show`

```text
$ python cli.py runs show arms/onetbb.json
path:       arms/onetbb.json
kind:       arms (arms.v1)
schema:     valid
subject:    onetbb
model:      gpt-6.1-sol
time:       2026-10-29T14:02:11Z
run_id:     - (no artifact records one yet)
size:       3,503 bytes
contents:   arms: 4, answers: 0
next to it: onetbb.md, plugin_delta.json (plugin_delta.v1)
```

The path is the one `runs list` prints, relative to the root. An absolute
path, or one from the working directory (`results/arms/onetbb.json`), works
too if it lies inside the root. The artifact is checked against the schema of
the version it declares. An artifact that fails the check prints
`schema: invalid, N errors` and each error, and the command still exits 0:
`runs show` reads, it does not gate. `contents` gives the length of the lists
the kind holds, and `next to it` the first 20 entries of the artifact's
directory. `--json` prints the fields of a `runs list` row plus `root`,
`schema`, `schema_valid`, `schema_errors`, `contents`, `siblings`,
`siblings_total` and `siblings_error`.

## Exit codes

| Case | Exit |
| --- | --- |
| Listed or shown, including an artifact that fails its schema | 0 |
| `results/` does not exist (`runs list` prints "no runs") | 0 |
| `DOKIMI_RUNS_DIR` does not exist, or the root is not a directory | 2 |
| `runs show`: no root, a path outside the root, a missing file, a directory, or a file that is not a readable artifact of a registered kind | 2 |
