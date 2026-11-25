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
{"kind": "probe", "target": "cpu", "pattern": "nproc", "text": "nproc", "rc": 0, "t": 12.4, "v": 2}
{"kind": "turn", "turn": 3, "input": 1200, "output": 85, "cache_read": 9000, "cache_write": 0, "prompt_tokens": 10200, "t": 14.0, "v": 2}
{"kind": "compaction", "turn": 7, "t": 40.5, "v": 2}
```

| Kind | Fields |
|---|---|
| `command` | `text`; `rc` (integer, or `null` when no result came back); `rc_from: "success"` when Copilot reported a completion status without an exit code; `output` and `output_truncated` when the harness returned output; `searched` (a list of workspace paths) when a `grep`, `egrep`, `fgrep`, `rg` or `git grep` part of it named files and showed lines of them (see below) |
| `read` | `path` (absent on a refused relative read whose file is not known; a relative path joined to the working directory the CLI started in loses its `.` components and repeated `/`, while an absolute path, or a relative one with no working directory known, is kept as written; a `..` always stays as written, since the name before it may be a link); `rc` 0 or 1 when the harness reported one |
| `edit` | `path`; `rc`; `deleted` when the file was deleted |
| `tool` | `name`; `skill` on a `Skill` call; `searched` (one path) on a Claude Code `Grep` call whose `path` is given and whose `output_mode` is `content` |
| `probe` | `target`, `pattern`, `text`, `rc`: written by the runner after the `command` event it matched, never by a converter ([§4.1](repo-evaluation-contract.md#41-eventsjsonl)) |
| `choice` | `candidate`, `act` (install, import, use or remove), `path` (edit path or null). The runner writes these after their source event when the run names candidates. |
| `turn` | `turn` (the model call's index in the trace, from 0; a subagent's calls count too); `input` (uncached input), `output` (on Claude, the call's `output_tokens` as its assistant records streamed them: records that share a message id merge to the largest count, and the streamed count can be below the billed one, see `usage_remainder`), `cache_read`, `cache_write`, `prompt_tokens` (the whole prompt: `input + cache_read + cache_write`); each `null` when the harness reported no such count (a Claude `prompt_tokens` sums the parts it reported, so it is low when a part is `null`) |
| `compaction` | `turn`: the index of the turn it came after, `null` before the first call |
| `usage_remainder` | `input`, `output`, `cache_read`, `cache_write`: the result's token totals (the scope `usage_scope` names) minus the `turn` events of that scope, each `null` when a turn in the scope has a `null` for it; no `turn`, `t` is `null`. Written once, after the turns, on a Claude trace whose result has usage. Negative when the turns sum above the total |

A `turn` is one model call, so a Claude run's trace has one per call. A call
whose assistant record has no `usage` object writes no `turn`; a `compaction`
after it has `turn: null` unless an earlier call wrote one. On a complete Claude
trace, the `input`, `cache_read` and `cache_write` sums of its main-thread `turn` events (those without
`agent`) equal the result's own top-level `usage` (`input_tokens`, `cache_read_input_tokens`,
`cache_creation_input_tokens`): the parent's billed counts. The result's `modelUsage`
totals, which `result.json` reports, can exceed them: a helper model's call (a title or
a classifier) leaves no assistant record in the trace. So the `turn` sums plus the
`usage_remainder` line equal the totals `result.json` reports, in its scope.
`output` does not reconcile to the billed output the same way: the streamed counts can
sum to less than the result's `usage.output_tokens` (in one live run, 27 against 675),
so read a Claude `output` as the streamed count, and the remainder's `output` as its gap.
Over the same scope: with `usage_scope` `tree` the events of subagents are in the sum
(they carry `agent`), with `main` they are not. A
model that the result's `modelUsage` lists but that made no assistant record has
no `turn`; its tokens are in the `usage_remainder` line only. Codex
reports usage on each `turn.completed`, one `turn` event per turn, with `t`
`null`; a count Codex did not report is `null`. In a Claude trace the `turn` and `compaction` lines follow its tool lines, so sort those by `t`; Codex's lines are in trace order. Copilot's trace carries no
usage, so it writes no `turn` events and its token counts stay `null`.
`compaction` is the point where Claude Code summarised the history; it has no
tokens of its own.

Turn and compaction events are not tool calls: `result.json` `process.tool_calls`
counts the tool kinds only (`command`, `read`, `edit`, `tool`). A reader that
reads the tool path of a run keeps to those kinds too.

`searched` is the list the `process` count `guidance_searched` is built from
(contract [§4.1](repo-evaluation-contract.md#41-eventsjsonl)). It is on the event
of the call that searched, not a `read` event, so `tool_calls` is the same with
or without it; `repo_event.v2.json` declares it as a list of strings.
It names files as the command gave them (made workspace-relative,
the `cd` before the part applied), whether or not they exist or are guidance, and
every file a multi-file search named when the command showed a line, not only the
files that supplied one; a
call that was refused, a search that failed as one search of one file, a `Bash`
call, Codex command or Copilot `bash` command whose result is the empty text (a
`grep` into `head` exits 0 when nothing matched), a `Grep`
call whose result is empty or `No matches found`, and a relative file after the
shell moved leave it out.

Any line may also carry `agent` (the id of the parent's `Agent`/`Task` call that
started it), and `denied: true` when the call was refused and never ran. The
exact field list is the JSON Schema `dokimi/schemas/repo_event.v2.json` (lines this code writes);
lines written before version 2 are described by `repo_event.v1.json`, which is unchanged.

## Version

Every line carries `v`: the major version of the format it was written under.
This page describes version 2, which lines written by this code carry as `v: 2`.
Version 2 differs from version 1 in two ways: `output_truncated` is on `command`
lines only and `output` on `command`, `turn` and `usage_remainder` lines (version 1 had both on every line), and the `turn` and `compaction` kinds
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
