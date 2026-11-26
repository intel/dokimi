---
depends_on:
  - dokimi/dokimi/runs/**
  - dokimi/dokimi/commands/runs.py
---
<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

# The runs root: `runs list`, `runs show`, `runs rm` and `runs gc`

Catalog static receipts (`catalog_eval.v1`) are supported too: the subject is the
catalog root, and `runs show` counts skill rows and problems. Point `DOKIMI_RUNS_DIR`
at the dedicated `catalog eval` output directory to inspect those receipts. Their
static pass does not establish a skill's runtime outcome.

Most commands that run an evaluation write their artifacts under `results/` by
default (a few write elsewhere, listed below). `runs list` shows what is there,
one run's artifacts together, and `runs show` opens one artifact or one run. Both only read files: no model call, no
network access, nothing written. `runs rm` and `runs gc` remove runs from the root
([below](#removing-runs-runs-rm-and-runs-gc)).

The runs root is `results/` in the working directory. Set `DOKIMI_RUNS_DIR` to
read another directory.

## `runs list`

```text
$ python cli.py runs list
RUN                        SCHEMA           SUBJECT                     MODEL              TIME (UTC)         PATH
20261029T140211Z-3f9a0c1e  arms.v1          onetbb                      gpt-6.1-sol        2026-10-29 14:02   arms/onetbb.json
-                          plugin_delta.v1  onetbb                      gpt-6.1-sol        2026-10-28 10:11*  arms/plugin_delta.json
20261028T093512Z-77c2e0b4  eval.v1          -                           claude-sonnet-5-5  2026-10-28 10:05   onetbb_r7/eval/onetbb.json
20261028T093512Z-77c2e0b4  answers.v1       onetbb                      gpt-6.1-sol        2026-10-28 09:40   onetbb_r7/answers/onetbb.json
-                          novelty.v1       skill:skills/onetbb-tuning  claude-sonnet-5-5  2026-10-27 18:30   novelty/skill_skills_onetbb-tuning/novelty.json

5 artifacts under results/ (* the file's mtime: the artifact records no time)
runs: 2 (3 artifacts); no run_id: 2 (runs show PATH says why)
skipped: 1 JSON with no leading schema_version, 1 JSON of an unknown schema_version (pairwise.v0: 1), 1 not .json
```

- **Each row is one artifact file.** A file is an artifact when its first 256
  bytes open with the `schema_version` of a registered kind: `save_artifact`
  has written it as the first key since
  [#27](https://github.com/intel/dokimi/pull/27), well inside that. Superseded
  versions such as `questions.v1` count too. Files that open with anything
  else are skipped without being read in full, so a large file of another kind
  costs nothing.
- **Subject, model and time** come from the artifact's own fields, and which
  fields depends on the kind: `product` of a questions set, `library_name` of
  an arms run, `artifact.ref` of a novelty run, `repo.id` of a reach run,
  `judge_model` and `evaluated_at` of an eval. An artifact that records no
  subject shows `-` (an eval, a task run, a reach matrix, a questions set from
  `questions generate`, which records the model but not the product); nothing is guessed
  from the path. When an artifact records no time, its file's mtime is used
  for the order and shown with `*`. A time that is not ISO 8601 (`yesterday`) is
  printed as written, with no `*`, and the row is placed by the file's mtime.
- **The artifacts of one run are listed together.** `RUN` is the artifact's
  `run_id` (below), `-` when it has none. The runs are newest first, each in
  the place of its newest artifact, and the artifacts of a run are newest
  first too. An artifact with no `run_id` stands on its own, in its place by
  time. The summary counts the runs and the artifacts with no `run_id`.
- **Nothing is dropped silently.** The summary counts the files skipped, by
  reason: JSON that does not open with a `schema_version` (other output, and
  artifacts saved before #27, which did not put it first), JSON of an unknown
  `schema_version` (named), `*.tmp` and `*.rejected` files left by
  `save_artifact`, other files, entries that are not regular files (a FIFO),
  symlinked directories and Windows junctions, which are not walked, and
  symlinks to files outside the root, which are not read. A file or directory
  that cannot be read is
  listed as unreadable, as is a file that opens with a registered
  `schema_version` but does not parse (a file of no registered kind is never
  parsed). The text summary names the first one's reason; `--json` lists all.
- `--kind KIND` (repeatable, for example `--kind arms --kind eval`) and
  `--subject NAME` (an exact, case-sensitive match) filter the rows. The rows
  left are grouped, so a run's place follows the newest of its rows shown.
- `--json` prints one `cli_output.v1` document (`kind` `runs list`) with `root`, `artifacts` (in the
  order of the rows), `runs`, `total` (before filtering), `skipped`, `unknown_versions` and `unreadable`
  under `data`. Each run has `run_id`, `kinds` (sorted) and the `paths` of its artifacts, in the order of
  the rows; an artifact with no `run_id` is in no run. Each artifact has `schema_version`,
  `kind`, `run_id`, `subject`, `model`, `generated_at` (the time the artifact
  records, `evaluated_at` for an eval, `reviewed_at` for a question panel;
  `null` when none), `path` (relative to the root) and `size_bytes`.
  `status` is `partial` when `unreadable` is not empty, and the exit code stays 0.

**The `run_id`** is the run envelope's
([#277](https://github.com/intel/dokimi/issues/277), see
[the envelope](benchmarking-and-comparison.md#the-run-envelope)): the files one
process writes share it. It is `null` for a file saved before the envelope,
for an input kind such as a question set, and for a `run_id` that is not a run id
(`runs show` lists the schema error).

**What it does not show.** Files outside the root are not listed:

- `repo run` writes its batches to `repo-runs/`, and its files carry no
  `schema_version`;
- `answers generate` and `eval score pointwise` write to `answers/` and `eval/` unless
  `--name` is given, and `eval score panel` writes to `eval/`;
- `eval questions generate` and `eval questions refine` write to `questions/`,
  `eval questions review` and `eval questions analyze` to `reports/`,
  `eval personas discover` to `personas/`, `repo reach` to `reach-out/`,
  `docs check` to `baselines/current.json`, `baseline save` to `baselines/eval/`,
  `tools preference` to `tool_preference.json` and `dashboard generate` to
  `dashboard.json` in the working directory;
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
run_id:     20261029T140211Z-3f9a0c1e
size:       3,503 bytes
contents:   arms: 4, answers: 0
next to it: onetbb.md, plugin_delta.json (plugin_delta.v1)
```

The path is the one `runs list` prints, relative to the root. An absolute
path, or one from the working directory (`results/arms/onetbb.json`), works
too if it lies inside the root once symlinks are resolved: neither command
reads anything outside the root. The artifact is checked against the schema of
the version it declares. An artifact that fails the check prints
`schema: invalid, N errors` and each error, and the command still exits 0:
`runs show` reads, it does not gate. A file with no `run_id` shows
`- (saved before the envelope)`, `- (an input kind: no envelope)`, or
`- (not a run id: see the schema errors)`. `contents` gives the length of the lists
the kind holds, and `next to it` the first 20 entries of the artifact's
directory. `--json` prints one `cli_output.v1` document (`kind` `runs show`) whose `data` holds the
fields of a `runs list` row plus `root`, `schema`, `schema_valid`, `schema_errors`, `contents`,
`siblings`, `siblings_total` and `siblings_error`. `runs show RUN_ID --json` has `status` `partial`
when `unreadable` is not empty.

### `runs show RUN_ID`

```text
$ python cli.py runs show 20261028T093512Z-77c2e0b4
run_id:     20261028T093512Z-77c2e0b4
artifacts:  2 (answers: 1, eval: 1)

SCHEMA      SUBJECT  MODEL              TIME (UTC)        PATH
eval.v1     -        claude-sonnet-5-5  2026-10-28 10:05  onetbb_r7/eval/onetbb.json
answers.v1  onetbb   gpt-6.1-sol        2026-10-28 09:40  onetbb_r7/answers/onetbb.json
```

A `run_id` from `runs list` shows the artifacts that carry it, as `runs list`
rows, newest first, with the same `*` for a time taken from the file's mtime. A
file `runs show` can open (one that opens with the `schema_version` of a
registered kind) wins, and so does a file it cannot read or one that is not a
regular file (a FIFO), which gets its error: one of that name under the root, or
reached from the working directory and inside the root. The name is read as for
any path, under the root first, so a directory there shadows a file of that name
in the working directory. A directory of that name (a run's own,
`results/nightly-7/`) does not, nor does a path outside the root or a file under
the root that is not an artifact (a text file); when no artifact has the
`run_id`, such a name gets the error it got before. When a file or directory
under the root could not be read, the count and the first one are printed, since
it may belong to the run; a root that cannot be listed at all exits 2 and says
so. `--json` prints one `cli_output.v1` document (`kind` `runs show`) whose
`data` holds `root`, `run_id`, `artifacts` (the `runs list --json` rows) and
`unreadable`.

## Removing runs: `runs rm` and `runs gc`

A run is what `runs list` groups: the artifacts that carry one `run_id`. Removing a
run removes those files and nothing else. Files that are not artifacts (the
`onetbb.md` report beside an arms run) stay, and so do the directories, empty or not.
An artifact with no `run_id` (an input kind such as a question set, or a file saved
before the envelope) is in no run: only `runs rm PATH` removes it. Neither command
touches a path outside the root. Once a run has a directory of its own
([#94](https://github.com/intel/dokimi/issues/94) R2), that directory is what a run
removes.

```text
$ python cli.py runs rm 20261028T093512Z-77c2e0b4
removed run 20261028T093512Z-77c2e0b4: 2 artifacts, 7,006 bytes

SCHEMA      SUBJECT  MODEL              TIME (UTC)        PATH
eval.v1     -        claude-sonnet-5-5  2026-10-28 10:05  onetbb_r7/eval/onetbb.json
answers.v1  onetbb   gpt-6.1-sol        2026-10-28 09:40  onetbb_r7/answers/onetbb.json
```

- **`runs rm RUN_ID|PATH`** reads its argument as `runs show` does: a `run_id`
  that some artifact carries, when no artifact file has that name, removes the
  run; otherwise it is a path, relative to the root or absolute, and the one file
  there is removed, an artifact or not (a file that does not parse, say). The file
  must lie under the root once the directories on its path are resolved; a
  symlink is removed itself, never its target. A directory, a missing file, a
  path outside the root or a `run_id` no artifact carries exits 2.
- **`runs gc --older-than AGE`** removes every run whose newest artifact is older
  than `AGE`, an age in days such as `30d`. The time is the one `runs list` sorts
  by: the time the artifact records, or its file's mtime when it records none. A
  run with one artifact newer than that stays whole.
- **`--keep-baselines`** keeps the runs a saved baseline holds: each copy that
  `baseline save` listed in `baselines/eval/manifest.json` (in the working
  directory) is read, and a run whose `run_id` a copy carries is kept and named. A
  copy saved before the envelope holds no run. A copy that cannot be read exits 2
  before anything is removed, since the run it holds is not known.
- An entry under the root that cannot be read may be the newest artifact of a run,
  so while one is there `runs gc` removes nothing and exits 2, naming the first. `runs
  rm RUN_ID` removes the run and leaves such an entry in place, counted as `runs list`
  counts it.
- A file is removed only while it holds what was selected: its device, inode, mtime
  and size, as the scan read it (as `runs rm PATH` looked it up). A file a writer
  replaced since (a new run saved at the same path) stays, is named (`left in place: 1
  file replaced since selected`), and the exit code stays 0. A replacement in the
  instant between that check and the unlink is not caught: nothing locks the root.
- `--dry-run` prints the same lines, `would remove` in place of `removed`, and
  removes nothing.
- `--json` prints one `cli_output.v1` document (`kind` `runs rm` or `runs gc`)
  with `root`, `dry_run`, `runs` (as in `runs list --json`), `paths` and
  `size_bytes` (what was removed, or with `--dry-run` would be), `not_removed`,
  `changed` (the paths kept because they were replaced) and
  `unreadable` under `data`; `runs rm` adds
  `run_id` (`null` for a file), `runs gc` adds `older_than`, `keep_baselines` and
  `kept` (each `run_id` with its `baseline`). `status` is `partial` when
  `unreadable` is not empty (`runs rm RUN_ID` only).
- A file that cannot be removed (no permission) is named, the others are still
  removed (`removed run ...: 1 of 2 artifacts`, and for `gc` `removed 0 of 1 run ...` when
  a run lost none; it is in `not_removed`, not in `runs`,
  `paths` or `size_bytes`), and the command exits 5 (`status` `failed`, `error.code`
  `infra`).

## Exit codes

| Case | Exit |
| --- | --- |
| Listed or shown, including an artifact that fails its schema | 0 |
| `results/` does not exist (`runs list` prints "no runs") | 0 |
| `DOKIMI_RUNS_DIR` does not exist, or the root is not a directory | 2 |
| `runs show`: no root, a path outside the root (or a symlink that leads out of it), a missing file, a directory, or a file that is not a readable artifact of a registered kind | 2 |
| `runs show RUN_ID`: nothing of that name and no artifact under the root with that `run_id` (the error counts the entries that could not be read), or a root that cannot be listed | 2 |
| `runs rm`, `runs gc`: removed, or listed with `--dry-run`; nothing to remove; `runs gc` when `results/` does not exist | 0 |
| `runs rm`: no root, a directory, a missing file, a path outside the root, or a `run_id` no artifact carries | 2 |
| `runs gc`: no `--older-than`, an age that is not days (`30d`), an entry under the root that cannot be read, or with `--keep-baselines` a baseline manifest or copy that cannot be read | 2 |
| `runs rm`, `runs gc`: a file that could not be removed | 5 |
