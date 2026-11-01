<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

# Guidance rules against session logs

Measuring whether an AGENTS.md or CLAUDE.md helps means an ablation: run the
agent with and without a rule and compare outcomes. That is expensive, so
`context prefilter` picks which rules are worth one. It reads the coding
agent's own session logs and asks, per rule: did the situation the rule
describes come up, did the agent act on it, and did the user correct the agent
on it. The output is a ranked list of **candidates for ablation**. It is not a
verdict, and it never says a rule should be cut.

Trigger rate is only a prefilter. A rule that rarely comes up can be the one
that prevents the costly failure, so a rare or protected rule is listed and
never ranked as waste.

```bash
python cli.py context prefilter AGENTS.md "$HOME/.claude/projects/<project>/*.jsonl"
python cli.py context prefilter AGENTS.md "$HOME/.claude/projects/<project>/*.jsonl" --json
```

The guidance file is split into rules with the span segmentation the novelty
layer uses (`dokimi/novelty/spans.py`): each sentence, list item or table row
that has a keyword is a claim. Headings and code blocks are not claims. No
model is called; every match is lexical.

## What is measured per rule

- **Trigger.** An *episode* where the situation came up: a prompt the user
  typed, or a tool call's inputs (command, paths, search patterns, description),
  share at least half of the rule's keywords (stemmed, stop words dropped,
  rounded up). A rule with no prose keywords (only backticked commands or paths, or a
  protection tag) triggers on a prompt that names one of them.
  Repeats within the window after a trigger are the same episode.
- **Acted.** Within `--window` events either side of the trigger, a tool call
  ran a command or touched a path the rule names in backticks (`` `make ci-local` ``,
  `` `changelog.d/` ``). A command counts when it runs the named command at the start of
  a shell segment (after `uv run`, `sudo` or environment assignments), so an `echo`, a
  `grep` argument or a `#` comment that names it does not. A path counts when a tool call
  names it in a path input or shell command, excluding shell comments and echo/printf
  arguments (their redirection targets still count). A Windows path matches with backslashes too.
  A rule that names neither is not measurable here: its
  `acted` is `null` and it carries the flag `acted_not_measurable`.
- **Corrected.** Within `--window` events after the trigger, the user's next
  prompt (the first prompt after it, and only that one) pushes back (`no`, `don't`, `instead`, `forgot`, `revert`, ...) and
  shares a keyword with the rule or names its command or path. Two or more
  episodes with corrections on half or more of them carry `user_overrides`:
  check the wording before anything else.

Not counted: the agent's own text, tool results (what a file or command
printed), subagent briefs (a `user` record with `isSidechain`), and records
Claude Code injects (`isMeta`, such as CLAUDE.md echoed into the context). A
memory echo of a rule would otherwise trigger that rule on every session.

## Statuses

| Status | Meaning | Ranked |
| --- | --- | --- |
| `candidate_for_ablation` | Triggered in fewer than `--in-use-rate` of the sessions, or never. Ranked fewest triggered sessions first, then lowest acted share. | yes |
| `in_use` | Triggered in at least `--in-use-rate` of the sessions. | no |
| `rare_not_ranked` | Triggered in 1 through `--rare-max` sessions. Few sessions are too little evidence to call a rule unused. | no |
| `kept_protected` | The rule carries a protection tag: a negation, a version gate, an ordering constraint or a threshold (the same tags the novelty layer uses). Its failure case is rare by design. | no |
| `insufficient_corpus` | Fewer than `--min-sessions` sessions were read. | no |

Precedence: `kept_protected`, then `insufficient_corpus`, then a rule with no trigger
(a candidate), then `rare_not_ranked`, then `in_use`; the rest are candidates. `--rare-max`
takes 0 or more and `--in-use-rate` a number from 0 to 1. A file named twice (a repeat, an
overlapping glob, a symlink to it) is read once.

The table prints the same counts; the JSON has every claim with its status,
keys (commands, paths, keywords), counts, rates and flags. Defaults: window 10
events, at least 5 sessions, rare at most 3 sessions, in use at 20% of sessions.

The JSON is printed, not stored: dokimi writes no file in this format, so it
has no schema under `dokimi/schemas/`. `format_version` marks its shape. A
change that stores it must first add a versioned schema there.

## Session logs read

This slice reads **Claude Code** session logs: JSON lines under
`~/.claude/projects/<project>/`, with `user` and `assistant` records and
`tool_use` blocks. A quoted glob expands. A file that cannot be read, or holds
no prompt and no tool call, is reported and the rest are still read; the exit
code is 1 when any file failed.

**Goose** sessions are read from its SQLite store, `sessions.db` under the Goose
data directory (`~/.local/share/goose/sessions/` on Linux). Each session in the
store is one session here. The layout comes from the block/goose source
(session storage and message serialization), not from a sample store: a
message's `content_json` is a list of blocks, a `text` block of a user message
is a prompt, and a `toolRequest` block carries the tool's arguments. A message
whose `metadata_json` marks it not user-visible is not a prompt. The store is
opened read-only. Pass the `.db` file in place of, or next to, JSONL logs. The
reader is `dokimi/context/goose_log.py`.

## Privacy and offline use

The command reads session logs and the guidance file on the local disk and
prints counts and the guidance's own rule text. It prints no transcript text,
calls no model and opens no network connection. Warnings name a file, not its
content. The tests use synthetic fixtures only.

## Limits

- Keyword matching is lexical. A rule phrased with words the agent never uses
  for its situation has a low trigger rate for that reason alone, which is why
  a rule's text is read before any ablation, not after.
- "Acted" needs a named command or path. Most prose rules have none; for them
  the report says `null`, not zero.
- Corrections are read from the next prompts only, so a correction given much
  later is missed.

An LLM matcher could judge each episode's relevance and whether the agent
complied. It is not implemented here. A plausible shape: one call per session
over the rules whose keywords matched, which for a corpus of 200 sessions is
roughly 200 calls with a rule list and a short excerpt each. That is an
estimate, not a measurement.

Related: [context-window.md](context-window.md) (what one run's window held),
[run-consistency.md](run-consistency.md) (whether repeated runs agree),
[skill-novelty.md](skill-novelty.md) (the rule-level novelty of a skill).
