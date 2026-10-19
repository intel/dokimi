<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

# Traceability

How an acceptance criterion in the [catalog](journeys.md) is linked to the CLI
and to the tests that show it, what that evidence is worth, and how coverage is
checked. This page is the rulebook; it holds no progress. Current coverage is
the report CI generates on every run.

## Where each fact lives

Each fact has one home, so nothing is copied by hand:

| Fact | Home |
|---|---|
| Journey, use case and criterion text, with ids | [journeys.md](journeys.md) |
| Per criterion: delivery, CLI surfaces, evidence ids and kinds, stubs, gaps | `tests/outcomes/journeys.yaml` |
| The command and flag inventory | `build_parser()` in `dokimi/cli.py`, read at check time |
| Which tests are evidence | `@pytest.mark.outcome("<id>")` on the test |
| Whether evidence passed, on which commit and run | the generated coverage report (a CI artifact) |
| Plans, progress, ownership, design decisions | issues and pull requests, linked by URL |

The registry sits beside the user-outcome contracts (`tests/outcomes/*.json`)
and uses the same marker and the same session check, `pytest --outcomes`. It is
YAML, not JSON, so the contract loader, which reads `*.json` there, never takes
it for a contract. A criterion can point at one of those contracts with
`outcome:`; its evidence ids must then be ids of that contract.

## Ids

- Journeys are `J1`–`J14`. They are never renumbered; a retired journey keeps its
  id and is marked retired.
- Use cases are `UC-Jnn-nnn`, criteria `AC-Jnn-nnn`, with the journey number
  zero-padded. A use case sits under its journey's heading, a criterion under its
  use case's heading; the check refuses one that is not.
- Issues, PR descriptions and test docstrings cite these ids. The check refuses an
  id on any page in this directory that the catalog does not define.

## Registry fields

```yaml
AC-J04-003:
  delivery: available           # proposed | partial | available | retired
  enforce: true                 # gate this criterion in CI
  capability: guidance-reach    # stable name; survives a CLI rename
  surfaces: ["repo reach --dry-run"]   # resolved against build_parser()
  refs: [docs/repo-evaluation-contract.md#7-reach-probe-guidance_reachv1]
  outcome: null                 # an existing tests/outcomes/*.json id, if any
  decision_ref: null            # an issue or PR URL, when a decision matters
  evidence:
    - id: cjm-repo-reach        # the @pytest.mark.outcome id
      kind: cli_scenario
      invokes: ["repo reach --dry-run", "repo reach --scenarios --out"]
      stubs: [harness (fake executable), model]
  scope: what this evidence covers and what it does not
  gaps: [known missing evidence, in words]
```

`decision_ref` is optional. A criterion needs no design record of its own; when a
decision behind it matters, link the issue or PR where it was made.

## Independent dimensions

A criterion's state is several separate facts. None implies another.

| Dimension | Values | Set by |
|---|---|---|
| Delivery | proposed, partial, available, retired | the registry, reviewed in the PR that changes it |
| Evidence kind | static_reference, unit, integration, cli_scenario, live_agent, recorded_manual | the registry, per evidence id |
| Verification | pass, fail, error, inconclusive, not_run | the session that ran the tests |
| Fidelity | the real components, and the stubs listed per evidence | the registry (`stubs`); `[]` means none |
| Scope and provenance | commit, run id, date, Python, what the session did not run | the report |

Evidence kinds, from weakest claim to strongest:

- **static_reference**: a file, anchor or symbol exists.
- **unit**: a function behaves as stated, called directly.
- **integration**: several real components together, with the listed stubs.
- **cli_scenario**: the test goes through the CLI parser to the command, with the
  listed stubs. The check confirms the invocation (below).
- **live_agent**: a real harness and model ran. Not produced by the offline test
  suite; reported `not_run` there.
- **recorded_manual**: a person checked it and recorded where. Also `not_run` in
  a test session.

A unit or integration pass is never shown as a CLI scenario, and a CLI scenario
with stubs is never shown as a real-user journey. Stubs are named per evidence
row because they are what limits the claim: a scenario against a fake harness
shows the measurement and its artifact, not how any real harness behaves.

Some claims sound alike and are not:

- Instructions: correctness, reach, adherence and task effect are separate
  criteria ([J4](journeys.md#j4--check-agentsmd-or-other-instructions)).
- Third-party skills: a structure check cannot certify general usefulness.
- Context: telemetry about context size cannot certify downstream behaviour.
- Evals: schema validity, grader correctness, task relevance and coverage are
  separate criteria ([J7](journeys.md#j7--create-or-improve-evals)).

## What the check enforces

`pytest --outcomes` checks the registry at the end of the session.

Always, for every entry:

- Every `J`, `UC` and `AC` id is defined once, under the right parent; J1–J14
  all exist; a registry key is a criterion the catalog defines.
- Every `refs` path exists, and a `#anchor` is a heading of that markdown file.
- Every surface and every `invokes` entry is a leaf command of
  `build_parser()` with those flags, and contains no `--`.
- An enforced criterion with a `cli_scenario` has every surface covered by one
  of its `invokes` entries (the same command and all its flags).
- A `cli_scenario` names what it invokes; other kinds do not. Unit,
  integration and CLI evidence lists its stubs.
- `outcome` names an existing contract, and the evidence ids belong to it.

For `enforce: true` entries:

- The criterion is `partial` or `available` and has evidence a test session
  produces.
- Every evidence id is claimed by a test. An unclaimed id is an error.
- A claimed test that failed fails the criterion; all claimed tests skipped is
  inconclusive. Neither passes.
- A `cli_scenario` passes only if a passing claimed test parsed an argv with
  the `dokimi` root parser that reaches each declared command with its flags.
  "Invoked" means exactly that: the root parser accepted the argv with no
  leftover arguments; the command's handler need not have run. The plugin
  records those argvs while marked tests run, including from threads the test
  starts; a skipped or failed test contributes none. Options are compared as
  argparse reads them: `-j` counts as `--jobs`, an abbreviation argparse accepts
  counts as its option, and nothing after `--` is an option. Two options that
  share a `dest` (`--x`, `--no-x`) stay two options. A command written in a
  string, comment or docstring is not an invocation. The capture sees in-process
  parsing (`build_parser().parse_args(...)`, the way the suite drives the CLI); a
  subprocess running `cli.py` is not captured, so evidence of that shape is
  declared `integration`.

Entries with `enforce: false`, and catalog criteria with no entry, gate nothing
at run time: a failing or unclaimed test of theirs does not change the session
verdict. A broken link in such an entry (the checks above) is still an error.
They are listed in the report as unverified, with their gaps, even when their
tests pass: nothing would fail if those tests stopped proving the criterion.
A missing or unreadable catalog or registry is an error in the report, which is
still written.

The session exit code is the worst of the contract verdicts and the journey
verdict: 0 pass, 1 fail, 2 error, 3 inconclusive.

## Generating the report

```bash
pytest -n auto --outcomes --outcomes-report=outcomes-result.json --journeys-report=journeys-result.json
```

This writes `journeys-result.json` and `journeys-result.md`.
`--journeys-report` must end in `.json` (the markdown goes beside it); any other
suffix is a usage error before the tests run, exit 4. A missing parent directory
is created. CI runs it in the
test job and uploads both with the coverage artifact. The report holds the
inspected commit (`GITHUB_SHA` in CI, else `git rev-parse HEAD`), the run id,
the date, one row per documented criterion with its evidence, kinds, stubs and
verdicts, the stated gaps, and the list of unverified criteria.
Running a subset of the tests leaves enforced evidence unclaimed, so the
journey verdict of a partial run is an error by design; the full suite is the
run that counts.

Reports, runtime traces and the artifacts of paid runs are not committed.

## Changing the registry

- **New criterion, implemented.** Add it to the catalog, mark the tests, add the
  entry with `enforce: true`.
- **New criterion, planned.** Add it to the catalog; an entry is optional and
  carries `delivery: proposed`. It shows as unverified and blocks nothing.
- **Weakening.** Turning `enforce` off, dropping evidence or downgrading
  `delivery` is a visible change in the PR diff; say why in the PR. The check
  never relaxes an entry by itself, and there is no generated baseline to
  regenerate: the registry is the ratchet, and only a reviewed edit moves it.
- **A metadata correction** (the criterion was never really covered) is a valid
  reason to weaken. Correct the entry rather than keep a misleading pass.
- **Renaming a command.** Update the `surfaces` and `invokes` strings; the
  capability name and the criterion stay.

Who declares a whole journey complete is not decided. The check states, per
criterion, whether its evidence passed within its scope; an issue or PR records
review. Neither is a certification of the journey.
