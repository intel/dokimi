<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

# Reach conformance matrix

The reach conformance suite asks, for each agent harness and version, which guidance files it loads in a set of
small fixture repositories, and compares what it observed with what the harness's vendor documentation says. A
nightly workflow runs it and publishes the result as the **reach matrix**: harness, version, fixture, file,
scenario, observed state, documented state.

It answers the question behind agents.md issues #211, #213 and #135: which file a given harness actually reads,
and where its documentation and its behaviour differ.

## What a run is

A run is one harness, at one version and one model, over one fixture. It is `repo reach` (see
[repo-evaluation-contract.md](repo-evaluation-contract.md) §7) with `--format receipt`: the effective-instructions
receipt of §7.1 is written to the run's output directory, and each source's observed state is compared with the
documented one. A harness that cannot run here gets a `skipped` run and no cells.

The matrix has two kinds of record:

- `runs`: one per harness and fixture, with a status. `ran` means every scenario answered; `errors` means a
  receipt came back with a scenario that gave no usable answer; `failed` means no receipt (a timeout, or a
  crash before the receipt was written); `skipped` means the harness could not run here, with the reason.
- `cells`: one per source file per scenario, with the observed state, the documented state and an agreement:
  `agrees`, `disagrees`, `inconclusive` (observed unknown, or only `fetched`: reach saw the file only in a `read=` scenario, after the agent read it with a tool, so its loading rule is not shown), `no_claim` (the docs say nothing),
  or `alias` (a link, whose state is its target's and is not compared).

The schema is `reach_matrix.v1` (`dokimi/schemas/reach_matrix.v1.json`).

## The fixtures

Each directory under `data/reach-conformance/` is one case. Each has a root `AGENTS.md` or `CLAUDE.md`
arrangement that exercises one loading rule.

| case | what it holds | the rule it tests |
|---|---|---|
| `root-agents` | `AGENTS.md` at the root | the baseline: a root file is read at the root |
| `nested-agents` | a root `AGENTS.md` and `packages/app/AGENTS.md` | a subdirectory file loads when cwd is inside it, or (Claude Code) when a file there is read |
| `claude-and-agents` | `CLAUDE.md` beside `AGENTS.md` | Claude Code reads `AGENTS.md` only when no `CLAUDE.md` is in scope |
| `claude-imports-agents` | `CLAUDE.md` containing `@AGENTS.md` | a Claude Code `@` import is followed; Codex does not read `CLAUDE.md` |
| `override-file` | `AGENTS.md` beside `AGENTS.override.md` | Codex uses the override in place of `AGENTS.md` |
| `subtree-claude-shadow` | `sub/AGENTS.md` beside `sub/CLAUDE.md` | a directory's `CLAUDE.md` shadows its `AGENTS.md` in Claude Code |
| `large-file` | a root `AGENTS.md` of about 40 KB | the size cap; the docs give no cut-off for Claude Code, and 32 KiB for Codex |
| `symlinked-file` | `CLAUDE.md` as a symbolic link to `AGENTS.md` | how a link is named and read; the docs do not say |

The fixtures are plain directories. `guidance.yaml` next to them is a `repo_manifest.v1` that names every
guidance file glob (`AGENTS.override.md` included), so every harness probes the same set of files whatever its
own profile names. Each fixture holds only guidance files: no tasks and no code.

## Where the documented states come from

`data/reach-conformance/expectations.yaml` (`reach_conformance.v1`) holds, for each case and harness, the
documented state of each file in each scenario, and a `why` in words. The sources are the vendor pages:

- Claude Code: `code.claude.com/docs/en/memory`, the "AGENTS.md" and "How CLAUDE.md files load" sections.
- Codex: `learn.chatgpt.com/docs/agent-configuration/agents-md`, and the `project_doc_max_bytes` and
  `project_doc_fallback_filenames` settings.

A file or scenario the expectations do not list is `unknown`. Then the cell's agreement is `no_claim`: the
observation is reported, and nothing is claimed about it. Copilot has no documented start-up behaviour for its
CLI, so every Copilot cell is `no_claim`; a Copilot run still records what it observed.

Scenario ids are `cwd=.` (start in the root), `cwd=<dir>` (start in a subdirectory) and `read=<dir>` (start in
the root and have the model read a file in `<dir>`).

`python scripts/reach_conformance.py check` verifies the expectations against the fixture tree: every fixture
has an entry, every named file exists in its fixture, and every named scenario's directory exists. It exits 1
on any drift, and `run` refuses to start while there is drift.

## Running it

From the dokimi project directory, with the dependencies installed (`make dev`), and a harness CLI on `PATH`. Set
`PYTHONPATH=.`: `make dev` installs the dependencies, not the package, and a script's own directory is what Python
puts on the import path.

```sh
PYTHONPATH=. python scripts/reach_conformance.py check
PYTHONPATH=. python scripts/reach_conformance.py run --harness codex=<model> --out reach-out
PYTHONPATH=. python scripts/reach_conformance.py merge reach-out/*.matrix.json --out reach-matrix.json --md reach-matrix.md
```

`--harness NAME[=MODEL]` is repeatable: `claude-code`, `codex` and `copilot` are known. A harness without a model
is skipped, as is one without its credential or its CLI (`unavailable` in `dokimi/repos/conformance.py`
names which). `run` writes `<harness>.matrix.json` to `--out`, and the per-fixture receipts under
`<harness>-runs/`. Each scenario is capped by `--timeout` (seconds per scenario, default 300). `--budget` (USD,
per scenario, default 0.10) is passed to Claude Code as `--max-budget-usd` only: Codex and Copilot have no budget
flag, so for them the timeout is the only cap. `run` exits 1 only when a run is `failed`. `run` gives each
`repo reach` process exactly the environment it was started with, and each harness gets only what its profile
forwards from that.

Credentials are the names the harness profile forwards (`dokimi/repos/profiles/*.yaml`, `env_allow`):
`ANTHROPIC_API_KEY` or `AWS_BEARER_TOKEN_BEDROCK` for Claude Code, `CODEX_API_KEY` for Codex (`OPENAI_API_KEY` is
not forwarded), `COPILOT_GITHUB_TOKEN` for Copilot. Codex runs with `dokimi/repos/conformance_profiles/codex.yaml`,
which defines a provider that reads `CODEX_API_KEY` (`codex exec` sends no bearer without one), so the key is what
authenticates the run. For Bedrock, also set `CLAUDE_CODE_USE_BEDROCK=1` and
`AWS_REGION`: Claude Code does not select Bedrock from the token alone.

`merge` joins matrices from several harnesses or runs. It refuses a second run of the same harness version on the
same fixture, so a merge cannot double-count (failed and skipped runs hold no cells and are kept as they are). Each
matrix carries `expectations_digest`, the sha256 of the expectations it was scored against; `merge` refuses matrices
with different digests, and the `merge` command refuses ones whose digest is not that of its `--expectations` file.

The offline tests (`tests/test_reach_conformance.py`) cover the agreement rules, scenario ids, alias handling,
skipped and failed runs, merge, rendering, the drift checks, and the CLI, with the harness runs faked. No test
calls a model.

## Limits

- **Marker self-report, not behaviour.** `observed` is what the model reported seeing in its prompt, through marker
  tokens (see §7). A model can under-report, and a `loaded` cell does not show the instruction was followed.
- **Links.** A symbolic link is reported as an alias of its target. The receipt cannot show a harness ignoring
  the link's name, so an `alias` cell is not evidence that a harness reads or skips it.
- **Unknown is not false.** `unknown` in the documented column means the docs are silent, not that the file is
  not loaded.
- **One version per run.** The version is the one the harness reports; a matrix shows the versions that ran.
- **Cost.** Each scenario makes at least one model call. Each Claude Code scenario is capped at `--budget`, not
  the whole fixture or suite (a fixture with three scenarios can spend three times the cap); Codex and Copilot
  scenarios are bounded only by `--timeout`. The nightly run covers every scenario of every fixture for each
  harness that has a model, a credential and a CLI.

## Nightly workflow

`.github/workflows/reach-matrix.yml` runs on a schedule (daily, 04:37 UTC) and on `workflow_dispatch`. It is not a
pull-request check, and it has no `pull_request` trigger.

- One job per harness. Each checks its credential, installs its CLI only when the credential and the model
  variable are both present (pinned versions: `@anthropic-ai/claude-code`, `@openai/codex`, `@github/copilot`), and
  runs `run` with the model from a repository variable (`REACH_MODEL_CLAUDE_CODE`, `REACH_MODEL_CODEX`,
  `REACH_MODEL_COPILOT`). With no variable, the harness is recorded as skipped.
- Each job gets only its own secret in its environment. The Claude Code job uses the Anthropic key when it is set;
  otherwise the Bedrock token, with `CLAUDE_CODE_USE_BEDROCK=1` and `AWS_REGION` from the variable of that name.
- The `matrix` job checks that all three matrices arrived and fails, publishing nothing, when one is missing (its
  harness job died before writing it). It merges the three artifacts, appends the page to the run summary, and
  uploads `reach-matrix.json` and `reach-matrix.md` as the `reach-matrix` artifact (90 days). Each harness's matrix
  is a 30-day artifact of its own.
- `permissions: {}` at the top; each job has `contents: read`.

To enable a harness, set its model variable and its secret. Nothing is committed: no key, token or model string
is in the repository.

Secrets: `ANTHROPIC_API_KEY` (or `AWS_BEARER_TOKEN_BEDROCK`), `CODEX_API_KEY`, `COPILOT_GITHUB_TOKEN`. Variables:
`REACH_MODEL_CLAUDE_CODE`, `REACH_MODEL_CODEX`, `REACH_MODEL_COPILOT`, and `AWS_REGION` for the Bedrock path.
Runners: `intel-ubuntu-latest` on the Intel organisation, `ubuntu-latest` elsewhere; each job sets up Node.js for
its CLI install.

## Adding a case or a harness

A case is a directory under `data/reach-conformance/` with a root guidance arrangement, plus an entry in
`expectations.yaml` for every harness whose documentation says something about it. `check` must pass.

A harness is a `Harness` in `HARNESSES` (`dokimi/repos/conformance.py`), with its CLI and the credential
names its profile forwards, a profile under `dokimi/repos/profiles/`, its npm pin in the workflow, and a
job in the workflow. Its documented states go in `expectations.yaml` with the vendor page as `why`.

Both schemas are versioned (`reach_conformance.v1`, `reach_matrix.v1`). Compatible widening may edit the existing
version; breaking changes require a new version, updating `SCHEMA_VERSIONS` and retaining the old version in
`SUPERSEDED_VERSIONS` (`dokimi/artifacts.py`).
