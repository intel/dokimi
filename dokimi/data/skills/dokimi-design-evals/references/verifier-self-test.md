# Verifier self-test

Run these before any paid agent run. None of them calls a model, except the
judge check. Commands run from the dokimi project directory.

## 1. Schema and paths

Repository tasks (`repo-eval.yaml` + `task.json`):

```bash
python cli.py repo validate <source> --manifest <path/to/repo-eval.yaml> --fail-on-warning
```

Harbor tasks: there is no validate command. `tasks list` only shows that the
task was discovered (it reads `task.toml` and `instruction.md`); it never prints
an error row, and it lists a task that has no `tests/test.sh`, `solution/` or
`environment/`. A task without `instruction.md` aborts the listing with a
traceback. So a listed task is not known to be runnable: the first check that
the files Harbor needs exist is the `docker-oracle` run in step 2.

```bash
python cli.py tasks list --tasks-root <tasks-root>
```

## 2. Oracle passes, untouched workspace fails, isolation

Repository tasks: one command does all three (null, oracle, the agent cannot
read tasks or source; `docs/repo-evaluation-contract.md` §4.3). It exits 1 on
any broken or unsupported task.

```bash
python cli.py repo tasks check <source> --manifest <path/to/repo-eval.yaml> --task <id>
```

Harbor tasks: oracle and isolation have harnesses; expect `pass_rate: 1.0`
from the oracle and a clean probe.

```bash
python cli.py tasks run --tasks-root <tasks-root> --tasks <id> --harnesses docker-oracle --out-json results/oracle.json
python cli.py tasks run --tasks-root <tasks-root> --tasks <id> --harnesses docker-probe --out-json results/probe.json
```

**Manual for Harbor:** there is no null harness. Copy the task directory to a
scratch tasks root outside the suite, make the copy's `solution/solve.sh` do
nothing (`#!/bin/sh` and `true`), and run `docker-oracle` on the copy, so the
verifier runs on the untouched workspace with the harness's own mounts. Expect
`pass_rate: 0.0`.

```bash
python cli.py tasks run --tasks-root <scratch-root> --tasks <id> --harnesses docker-oracle --out-json results/null.json
```

A task that passes the oracle but also passes untouched does not discriminate.
Fix the assertion, not the oracle.

## 3. Mutation controls (manual: no command yet)

dokimi has no mutation-control command. Do it by hand with the commands above,
changing only the reference solution so it produces the mutated end state:

- Harbor: copy the task directory to a scratch tasks root outside the suite,
  edit the copy's `solution/solve.sh`, run `docker-oracle` on the copy. Expect
  `pass_rate: 0.0`.
- Repository: write a scratch copy of `repo-eval.yaml` whose `oracle` command
  runs the real oracle and then the mutation, and pass it with
  `--manifest-file` to `repo tasks check`. Expect the oracle check to fail.

The verifier must **fail** every mutated solution. Delete the copies; never
commit them.

Pick only the mutations that fit the task. Write the chosen list in the
scenario's `mutations` field, and why the others do not apply.

| Mutation | Mutated solution | Fits when |
|---|---|---|
| skipped step | the oracle minus one mandatory step (no rebuild, no migration, config not updated) | the task has a step the outcome depends on |
| false test claim | the change without the fix, plus a summary or file claiming tests pass | the task asks for tests to be run or reported |
| deleted tests | the fix plus deleting or skipping the failing test | the workspace has tests the agent could touch |
| weakened criterion | the fix replaced by loosening an assertion, tolerance or check | the success criterion lives in files the agent can edit |
| wrong tool | the right end state reached through a tool or path the user forbade | the request forbids a tool or names a required one |

**Valid inaction.** When declining, asking, or leaving the tree unchanged is
a correct answer (a negative task, an out-of-scope request), set
`valid_inaction: true`: the null workspace is then expected to **pass**, and
the "untouched fails" check does not apply. Use a mutation that does act
wrongly instead (an unrequested edit). Do not reword the task so that
inaction fails just to satisfy the template. Note that `repo tasks check`
always requires the null to fail; a valid-inaction task needs a grader that
fails on the unwanted action and its own manual null check.

## 4. Judged verifiers

A rubric judge must tell good answers from degraded ones before its scores
count. It costs model calls; the sequential mode stops early.

```bash
python cli.py eval judge-validity --answers <answers.json> --sequential --strict
```

Then have a person review a sample of judged cells against the rubric and
record the agreement. A run that hits its sample cap without a decision is
not a pass.

## What a passing self-test does not show

It shows the verifier is sane on the cases tried. It does not show the tasks
are relevant, or that the suite covers the subject; say so in the report.
