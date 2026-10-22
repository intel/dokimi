---
depends_on:
  - dokimi/dokimi/context/**
  - dokimi/dokimi/commands/context.py
---
<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

# Context window of a run

The other tracks score what an agent produced. `context trace` reads what the
agent *held* while producing it: how big the prompt was at every model call,
what start-up loaded before the agent did anything, how fast the history grew,
whether the harness compacted it, and which tool results took the room. It
reads a trace file and calls no model.

```bash
claude -p "<task>" --output-format stream-json --verbose > run.jsonl
python cli.py context trace run.jsonl --window 200000 --calls
```

```text
| trace | calls | first | peak | fill | growth | compact | tools | failed | repeat | result chars |
|---|---|---|---|---|---|---|---|---|---|---|
| run.jsonl | 2 | 21,390 | 21,673 | 11% | 283 | 0 | 1 | 0 | 0 | 165 |
run.jsonl: largest tool results (chars): Bash 165
run.jsonl: prompt tokens per call: 21390 21673
```

## What the columns mean

- **calls**: model calls on the main thread. Claude Code writes one assistant
  message per content block, and the blocks of one call share a message id, so
  a call is counted once. Calls a subagent made run in their own window and are
  counted apart (`subagent_calls` in `--json`).
- **first**: the prompt of the first call, `input + cache_creation +
  cache_read` tokens. It is everything start-up loaded (system prompt, tool
  schemas, memory and guidance files, the skill listing) plus the task.
- **peak**, **growth**: the largest prompt, and how far it rose above
  **first**: what the history added. **fill** is peak over `--window`, shown
  only when you pass the window, because the trace does not say it.
- **compact**: `compact_boundary` events. After one the prompt can drop; the
  `--calls` series shows by how much, and `--json --calls` gives each event's
  trigger, `pre_tokens` and (on newer CLIs) `post_tokens` as
  `compaction_events`.
- **tools**, **failed**, **repeat**: tool calls, calls whose result came back
  as an error, and calls identical in name and input to an earlier call on the
  same thread that did not fail, with no `Bash`, `Edit`, `MultiEdit`,
  `NotebookEdit` or `Write` call in between. A retry after an error and a
  `Read` after an `Edit` are not repeats; what is left is an agent fetching
  again what it already had.
- **result chars**: the size of every tool result, in characters, since the
  trace gives no token count for them. The line under the table names the
  tools whose results weighed most.

`--json` prints a list with one object per trace, also for a single trace. A
trace that cannot be read is reported on stderr and left out; the others still
print, and the command exits 1.

## Comparing context layers

Several traces print as one table, so the layers of a context compare side by
side. Run the same task in each condition and pass the traces together:

| Condition | What differs | Read |
|---|---|---|
| Free | an empty workspace | **first** is the harness's own baseline |
| Loaded | + AGENTS.md / CLAUDE.md, skills, MCP servers | **first** minus the free run's is what the guidance costs on every call |
| Execution, without skill | the task, guidance loaded | **peak**, **growth**, **calls** |
| Execution, with skill | the same task, skill installed | the difference is what the skill cost or saved over the run |

Two things make a baseline wrong without looking wrong. Claude Code loads every
`CLAUDE.md` from the working directory up to `/`, so run the free condition in
a directory with no guidance file above it (the system temp dir usually
qualifies). And a user-level `~/.claude/CLAUDE.md` loads into every run;
point `CLAUDE_CONFIG_DIR` at an empty directory to leave it out. The
`surface` field of `--json` (the tool, skill and agent counts, and the names of
the MCP servers and loaded plugins) shows what start-up offered, so two runs
that differ there are not the same condition.
