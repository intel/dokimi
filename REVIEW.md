<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

# Review guide

For anyone reviewing a dokimi PR, person or agent. CI already enforces lint,
types, coverage, growth, import layers and test quality (`AGENTS.md`); do not
repeat what it checks.

`AGENTS.md` directs reviewer agents here. Copilot gets the same pointer from
`.github/copilot-instructions.md`. The author-side checklist is in the
"Before opening a PR" section of `AGENTS.md`.

Start with the full diff. Check each changed file for independent defects
before reproducing individual findings.

## What a defect looks like here

- A command gives a wrong number, verdict or exit code, or crashes where it
  should print a message.
- A test passes when the behaviour it names is broken.
- A doc, docstring, comment, PR title or changelog fragment says something the
  code does not do.
- A stored record is written or read with keys or a shape other than the
  schema's.
- Behaviour differs on Windows or with a `None`, empty or missing input.
- A new `raise`, `sys.exit` or refusal that no doc, contract or issue asks
  for, or a test of new behaviour that cites no doc line: ask which line it
  traces to. Code built on a guess at an unclear requirement is a defect too;
  the question belongs in the PR's "Open questions". From the repo root, run
  `.venv/bin/python dokimi/scripts/list_new_error_paths.py --base "$base_ref"`
  with `base_ref` set to the PR's upstream base. It lists `raise`, `sys.exit`,
  `SystemExit` and literal `exit_code=N` in the committed diff. Check
  uncommitted edits, `parser.error` and numeric returns directly. On a task
  base without this script, check new error paths in the diff directly.

## Patterns that hide a defect

Flag these in code the PR adds or changes. Each is must-fix when it changes
a number, verdict or exit code, or when it matches a defect listed above.
Otherwise it is a nit, except the last two bullets, which are style points
raised only when they change a number, verdict or exit code.

- Lost data or failure: a slice, clamp, fallback chain, `or` default, retry
  or lenient parse that drops input or hides which path ran, with no warning,
  error or record of it.
- An error rewritten to a generic message, or reported as `False`, `None`,
  `-1` or `""` that the caller cannot tell from a result.
- A return value that matters left unused: a subprocess return code, a
  validation result nothing acts on.
- A missing directory or file created where a wrong path should fail.
- A mode, kind or state passed as a bare string and compared in several
  places, where an `Enum` or `Literal` would let mypy check it.
- A default on a parameter that switches behaviour, so a caller gets the mode
  without asking for it.
- A `None` check, `or` default or defensive copy on a value that cannot be
  `None` or is never mutated.
- A test that sleeps a fixed time or starts a `threading.Timer` to wait for
  another thread or process, or asserts on elapsed time or on a ratio of two
  timings: it passes on a fast machine and fails on a busy runner. Wait on the
  condition with `tests/waiting.py`.
- Structure without a second user: a base class, protocol, registry, factory
  or wrapper class with one implementation or entry, or a class that only
  forwards calls.
- Code nobody asked for: an option or case the issue and docs do not name, a
  stub or placeholder return, scaffolding left half-done.
- A loop that `pathlib`, `itertools`, `collections`, `json` or `shlex`
  already does.
- A docstring that repeats the function name, or a comment that narrates
  the next line ("Now we...", "Step 1:").

## How to raise a finding

- Name the file and line, and show the input that breaks it: a command, a
  failing test or a mutation that the tests still pass. A finding found by
  running code lands far more often than one found by reading.
- Name the bug class and related sites within the PR's scope. Note any
  out-of-scope occurrences separately.
- Include an inline suggestion when a fix and its test are short.
- Mark each finding must-fix or nit. Post nits only after the must-fix
  findings, at most three; skip the style of `dokimi/changelog.d/` fragments,
  not their accuracy.
- A nit is one the author can take: no style point the configured ruff rules
  leave out (line length, quotes, import order), and no suggestion that fails
  CI, such as one that adds a line to a file at its growth baseline.
- CI checks comment blocks the PR adds or edits against the two-line limit.
  Suggest a short reason and a link when a comment carries backstory such as
  run IDs or alternatives tried.
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
