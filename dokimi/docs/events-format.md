<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

# `events.jsonl` format

`events.jsonl` is the normalized trace of one agent run in a repository batch
(`repo run`, contract [§4.1](repo-evaluation-contract.md#41-eventsjsonl)). Each
harness's stdout is converted to it, so graders, probes and the security scan
read one format whatever harness ran. A cell's trace lives under its directory
and in `traces/` for `reach` runs.

## Lines

One JSON object per line. Every line has a `kind` and a `t`; the other fields
depend on the kind:

```json
{"kind": "command", "rc": 0, "t": 12.4, "text": "bazel test //x", "v": 1}
{"kind": "read", "t": 3.1, "path": "AGENTS.md", "v": 1}
{"kind": "edit", "t": 5.0, "path": "src/foo.cpp", "v": 1}
{"kind": "tool", "denied": true, "name": "WebFetch", "t": 20.2, "v": 1}
{"kind": "tool", "name": "Skill", "skill": "lint-fix", "t": 2.0, "v": 1}
```

| Kind | Fields |
|---|---|
| `command` | `text`; `rc` (integer, or `null` when no result came back); `output` and `output_truncated` when the harness returned output |
| `read` | `path` (absent on a refused relative read whose file is not known); `rc` 0 or 1 when the harness reported one |
| `edit` | `path`; `rc`; `deleted` when the file was deleted |
| `tool` | `name`; `skill` on a `Skill` call |

Any line may also carry `agent` (the id of the parent's `Agent`/`Task` call that
started it), and `denied: true` when the call was refused and never ran. The
exact field list is the JSON Schema `dokimi/schemas/repo_event.v1.json`.

## Version

Every line carries `v`: the major version of the format it was written under.
Version 1 is the format this page describes.

- A line with no `v` was written before the field existed. It has the version 1
  shape and reads as version 1.
- The version is per line, not in a header line. An empty file is what the runner
  writes for an agent that never started; a header would make it non-empty. A
  line also stays self-describing when a file is sliced or appended to.

## Promise

Within a major version, kinds and fields are only added. A reader written for
version 1 keeps working on every later version 1 file, and a field that existed
keeps its name, type and meaning. A change that would break a version 1 reader
(a renamed or removed field, a changed meaning, a changed `null`) is version 2:
a new schema `repo_event.v2.json`, with `v` set to 2 on the lines it writes. A
file may mix versions, since traces are re-derived and appended to.

## Reading

- Ignore a kind you do not know. Version 1 readers do so already: a new kind
  (say a `turn` event) must not fail an old reader.
- Ignore a field you do not know.
- Accept a line with no `v`. `dokimi.repos.event_format.event_version` returns 1
  for it.
- A line that is not a JSON object is skipped, as the readers in
  `dokimi/repos/` do.

## Null values

`t` is `null` when a record has no parseable timestamp (always for Codex). `rc` is
`null` when no result came back, for example at the turn or budget limit: a
grader must not count `null` as a failure.
