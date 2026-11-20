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
{"kind": "command", "rc": 0, "t": 12.4, "text": "bazel test //x", "v": 2}
{"kind": "read", "t": 3.1, "path": "AGENTS.md", "v": 2}
{"kind": "edit", "t": 5.0, "path": "src/foo.cpp", "v": 2}
{"kind": "tool", "denied": true, "name": "WebFetch", "t": 20.2, "v": 2}
{"kind": "tool", "name": "Skill", "skill": "lint-fix", "t": 2.0, "v": 2}
{"kind": "turn", "turn": 3, "input": 1200, "output": 85, "cache_read": 9000, "cache_write": 0, "prompt_tokens": 10200, "t": 14.0, "v": 2}
{"kind": "compaction", "turn": 7, "t": 40.5, "v": 2}
```

| Kind | Fields |
|---|---|
| `command` | `text`; `rc` (integer, or `null` when no result came back); `output` and `output_truncated` when the harness returned output |
| `read` | `path` (absent on a refused relative read whose file is not known); `rc` 0 or 1 when the harness reported one |
| `edit` | `path`; `rc`; `deleted` when the file was deleted |
| `tool` | `name`; `skill` on a `Skill` call |
| `probe` | `target`, `pattern`, `text`, `rc`: written by the runner after the `command` event it matched, never by a converter ([§4.1](repo-evaluation-contract.md#41-eventsjsonl)) |
| `turn` | `turn` (the model call's index in the trace, from 0; a subagent's calls count too); `input` (uncached input), `output` (`null` on a Claude turn: its assistant records carry the output count from the start of the stream, not the call's billed count), `cache_read`, `cache_write`, `prompt_tokens` (the whole prompt: `input + cache_read + cache_write`); each `null` when the harness reported no such count (a Claude `prompt_tokens` sums the parts it reported, so it is low when a part is `null`) |
| `compaction` | `turn`: the index of the turn it came after, `null` before the first call |

A `turn` is one model call, so a Claude run's trace has one per call. A call
whose assistant record has no `usage` object writes no `turn`; a `compaction`
after it has `turn: null` unless an earlier call wrote one. The `input`,
`cache_read` and `cache_write` sums of a complete Claude trace's `turn` events should equal the
run's `result.json` `input_tokens`, `cache_read_tokens` and `cache_write_tokens` when the result's
usage equals its per-call usage; its `output_tokens` is in the totals only. Over the same scope: with `usage_scope` `tree` the events
of subagents are in the sum (they carry `agent`), with `main` they are not. A
model that the result's `modelUsage` lists but that made no assistant record has
no `turn`; its tokens are in the totals only. Codex
reports usage on each `turn.completed`, one `turn` event per turn, with `t`
`null`; a count Codex did not report is `null`. In a Claude trace the `turn` and `compaction` lines follow its tool lines, so sort those by `t`; Codex's lines are in trace order. Copilot's trace carries no
usage, so it writes no `turn` events and its token counts stay `null`.
`compaction` is the point where Claude Code summarised the history; it has no
tokens of its own.

Turn and compaction events are not tool calls: `result.json` `process.tool_calls`
counts the tool kinds only (`command`, `read`, `edit`, `tool`). A reader that
reads the tool path of a run keeps to those kinds too.

Any line may also carry `agent` (the id of the parent's `Agent`/`Task` call that
started it), and `denied: true` when the call was refused and never ran. The
exact field list is the JSON Schema `dokimi/schemas/repo_event.v2.json` (lines this code writes);
lines written before version 2 are described by `repo_event.v1.json`, which is unchanged.

## Version

Every line carries `v`: the major version of the format it was written under.
This page describes version 2, which lines written by this code carry as `v: 2`.
Version 2 differs from version 1 in two ways: `output` and `output_truncated` are
on `command` lines only (version 1 had them on every line, which an integer
`output` on a `turn` line contradicts), and the `turn` and `compaction` kinds
are in it. Version 1 lines stay valid under `repo_event.v1.json`.

- A line with no `v` was written before the field existed. It has the version 1
  shape and reads as version 1.
- The version is per line, not in a header line. An empty file is what the runner
  writes for an agent that never started; a header would make it non-empty. A
  line also stays self-describing when a file is sliced or appended to.

## Promise

Within a major version, kinds and fields are only added. A reader written for
version 1 keeps working on every later version 1 file, and a field that existed
keeps its name, type and meaning. A change that would break a reader of the
current version is a new version: a new schema file `repo_event.vN.json`, with
`v` set to N on the lines it writes. Released schema files are not edited in
place. A file may mix versions, since traces are re-derived and appended to.

## Reading

- Ignore a kind you do not know. Version 1 readers do so already: a new kind
  (say a `probe` event) must not fail an old reader.
- Ignore a field you do not know.
- Accept a line with no `v`. `dokimi.repos.event_format.event_version` returns 1
  for it.
- A line that is not a JSON object is skipped, as the readers in
  `dokimi/repos/` do.

## Null values

`t` is `null` when a record has no parseable timestamp (always for Codex). `rc` is
`null` when no result came back, for example at the turn or budget limit: a
grader must not count `null` as a failure.
