<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

# Run consistency: result, path, contract

An agent that solved a task once may not solve it again, and an agent that
solves it every time may get there by a different route each time. Those are
two questions, and a third sits beside them: did the route keep the rules the
task sets. `context consistency` reads repeated runs of one task from their
Claude Code `stream-json` traces and answers each one apart. It calls no model.

```bash
python cli.py context consistency \
  --arm no-skill='runs/base/*.jsonl' --arm skill='runs/skill/*.jsonl' \
  --outcomes verdicts.json --contract contract.yaml
```

| Axis | Question | Numbers |
|---|---|---|
| Result | Does it solve the task every time? | `success_rate`, `pass@k` (any of k), `pass^k` (all of k, τ-bench) |
| Path | Do the runs take the same steps? | `mix_jsd` (same tools in same amounts), `order_distance` (same order), `action_distance` (same files and commands), CV of tool calls, turns, output tokens |
| Contract | Did each run keep the required process? | `conformance_rate`, violations per rule, `passed_but_nonconforming` |

The path is the main thread's steps. By default (`--shell split`) one `Bash`
call is one step per command it chains (`&&`, `||`, `;`, newline outside
quotes; heredoc bodies and comments dropped), each named by program
(`Bash:pytest`, `Bash:sed`), and a command that writes a file (`sed -i`, `>`,
`>>`, `tee`) carries it as `file_path`. Without this a model that does
`sed -i … pricing.py; sed -i … CHANGELOG.md; sh finish.sh` in one call shows as
one step, its path looks perfectly stable and a contract sees no edit and no
check. `--shell call` keeps one step per tool call. Subagent calls are left
out; a `Task` call is one step.

A failed shell call marks every command it chains as failed: the trace does not
say which one failed, and a failed `make check && git diff` must not read as a
passing `git diff`. A command piped into another (`pytest | tail`) without
`set -o pipefail` has its status hidden: the call's status is the last stage's.
`last_ok` does not accept such a step as a passing check.

A Stop hook that blocked the stop is a failed `Hook:Stop` step. A hook that
passed leaves nothing in the trace, so a check the harness enforces is not
visible as a step; count it from the harness configuration.

## Where the outcome comes from

Pass `--outcomes`: a JSON object of trace path (or file name, or stem) to
`true`/`false`, written by a verifier that checked the workspace. Without it a
run passes when the harness ended cleanly (`result` event, `success`), which
says the agent stopped, not that the task is done. The report says which source
it used. A file name or stem that two traces share (`base/1.jsonl`, `skill/1.jsonl`)
is refused: key those by full path.

## Strategies

A task with two valid routes would read as unstable if every pair of runs were
compared. Runs are grouped by the order distance of their tool paths, repeats
collapsed (`--threshold`, default 0.34), and each group gets a verdict:

| Verdict | Reading |
|---|---|
| one strategy, stable success | reproducible process |
| several strategies, all stable success | valid diversity |
| one strategy, unstable | the route is right, execution breaks |
| one strategy, stable failure | a reproducible mistake |

A group is a shape, not a proof the shape is right; the result and the
contract judge that.

## Contract

Named actions match tool calls by tool (or kind, like `Bash:pytest`) and regexes
over the call's input. Rules constrain only what matters; any order they do not
constrain conforms.

```yaml
actions:
  guide: {tool: Read, file_path: 'AGENTS\.md$'}
  edit:  {tool: [Edit, Write, MultiEdit]}
  test:  {tool: Bash, command: '\b(pytest|make test)\b'}
  push:  {tool: Bash, command: 'git push'}
rules:
  - before: [guide, edit]      # the guide is read before the first edit
  - after_last: [edit, test]   # a test runs after the last edit
  - last_ok: test              # the last test did not fail
  - forbids: push
  - requires: test
```

An `edit` action with a write tool (`Edit`, `Write`, `MultiEdit`) also matches a shell
step that writes a file (`sed -i`, `echo`/`printf`/`cat` into `>`, `tee`), so an edit
through the shell counts. Another program's output saved to a file (`pytest > test.log`,
`ls > /tmp/files.txt`) is not an edit.

Name every equivalent way to do a step: `make check`, `sh tools/check.sh` and
the skill's `finish.sh` are one check. A contract that knows only `make check`
reports agents that ran the script behind it as never checking.

`passed_but_nonconforming` counts runs the verifier passed that broke a rule:
what a result-only score hides, such as a change that happened to be right but
was never tested.

## Designing the experiment

Keep the sources of variation apart; one number over all of them explains
nothing.

| Series | What changes | What it measures |
|---|---|---|
| Repeat | nothing; the workspace reset each run | the agent's own variance |
| Paraphrase | the wording, not the task | robustness to the prompt |
| Fault | injected tool errors, timeouts | recovery |

A fault measures recovery only in runs that reach the faulted step. Put it on a
step the procedure requires and report how many runs reached it; an arm whose
agents never run the check is not robust to a check failure, it is untested.

Compare arms (no skill, prose skill, skill with scripts, skill with executable
checks) within one series. The useful finding is which uncertainty a skill
removed: the tool choice (`mix_jsd`), the order (`order_distance`), the targets
(`action_distance`), the failure handling (`last_ok`, failed calls) or the
final check (`after_last`).

Background: *Towards a Science of AI Agent Reliability*
(arXiv:2602.16666) for the outcome / trajectory-distribution /
trajectory-sequence / resource split; *Consistency as a Testable Property*
(arXiv:2605.10516) for strategy grouping; Yao et al., τ-bench
(arXiv:2406.12045) for `pass^k`; process-mining conformance checking for the
contract.
