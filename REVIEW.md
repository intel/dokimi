<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

# Review guide

For anyone reviewing a dokimi PR, person or agent. CI already enforces lint,
types, coverage, growth, import layers and test quality (`AGENTS.md`); do not
repeat what it checks.

Review bots (Copilot, the Claude Code Review app) read this file. Coding agents
read `AGENTS.md`, whose "Before opening a PR" section holds the author-side
checks.

## What a defect looks like here

- A command gives a wrong number, verdict or exit code, or crashes where it
  should print a message.
- A test passes when the behaviour it names is broken.
- A doc, docstring, PR title or changelog fragment says something the code
  does not do.
- A stored record is written or read with keys or a shape other than the
  schema's.
- Behaviour differs on Windows or with a `None`, empty or missing input.

## How to raise a finding

- Name the file and line, and show the input that breaks it: a command, a
  failing test or a mutation that the tests still pass. A finding found by
  running code lands far more often than one found by reading.
- Name the bug class and every other site that has it, so one round fixes all
  of them.
- Where a fix and a test are short, push them or put them in a suggestion
  instead of describing them.
- Mark each finding must-fix or nit. Post nits only after the must-fix
  findings, at most three; skip the style of `dokimi/changelog.d/` fragments,
  not their accuracy.
- A nit is one the author can take: no style point the configured ruff rules
  leave out (line length, quotes, import order), and no suggestion that fails
  CI, such as one that adds a line to a file at its growth baseline.
- A bug in code the PR did not change: mention it once as pre-existing. It
  does not block the PR unless the change makes it worse.
- Do not repeat a finding already raised on the PR, unless its fix did not
  hold. List still-open earlier findings by link.
- Start with the count of new findings, and say "nothing to fix" when there is
  nothing new and nothing still open.

## Rounds

- After the first round, review what changed since and the code that calls
  it.
- Re-run the original failing input on every fix, including fixes the
  reviewer pushed.
- If a PR is still collecting must-fix findings after three rounds, step back
  and agree on the approach in the thread before more commits.
- Close each thread with the commit that fixed it, or one line on why it is
  not a defect. The reviewer closes their own thread where they can.

## When the same comment keeps coming back

Move it upstream: a test, a check in `make ci-local`, or a line in `AGENTS.md`,
so the next PR cannot reach review with it.
