<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

# Preparing a repository for coding agents

> **Status: recommendations.** §1–§7 apply today. The measurement loop in §8 runs
> today for guidance arms (`guidance:none`, `guidance:raw`, `guidance:bridge`).
> `guidance:bridge` measures the `CLAUDE.md` import (§7). The `skill:` and
> `skill-catalog:` arms, which install skills, run today too
> ([skill arms](benchmarking-and-comparison.md#skill-arms-in-repository-mode)). Arms
> that add any other tool are planned; a changed guidance line can be tested today
> with two tasks that differ in a `setup_patches` entry (§8).

This page is for repository owners who want coding agents (Claude Code, Codex,
Copilot and others) to build, test, change and review their code reliably. It
lists what to put in the repository and its environment, and how to check with
Dokimi that each piece helps. Each figure links to the runs behind it (task
set, arms, model, cell count) on the [evidence page](preparing-a-repository-evidence.md) with anchors to the specific row; figures with no
stored result are given without a number.

Every tool and file below is an ordinary repository artifact that people and CI
use too. No tool is declared to Dokimi. Dokimi compares configurations: the same
tasks run with and without a change (an arm). See
[repository evaluation](repo-evaluation-contract.md).

The recommendations come from measurements on oneDAL, a C++ library.
Agents were Claude Code with Haiku and Sonnet 5.5, plus earlier generations
where noted, and Codex. Samples were small: 5 to 24 cells per arm. Some effects
disappeared when the model changed, so measure before you rely on any of them (§8).

## 1. Make every command in the guidance runnable

An agent follows the commands the guidance gives. A command that cannot run costs
turns and leads to work-arounds.

- **One command per action.** Format, build what changed, test what changed, lint.
  Prefer the build system and the repository's existing hooks (Bazel `rdeps`
  queries, CMake presets, pre-commit) over new wrapper scripts. CI, people and
  agents then run the same thing. Name the commands explicitly so agents find them.
- **The same version as CI.** If CI pins clang-format 20.1.8 and the environment
  has 18, every formatted file can fail the CI check.
    - With Haiku 4.5, a named command for the pinned formatter raised the share of
      runs that passed the CI format check from 5/16 to 12/16 (p = 0.016).
      Installing the right version without naming it reached 7/16. At 16 cells
      neither step alone is separated from noise: installing it, 5 to 7 (2 up,
      0 down, p = 0.5), and then naming it, 7 to 12 (5 up, 0 down, p = 0.062)
      ([E1](preparing-a-repository-evidence.md#e1)).
    - With Haiku 5.5, 12/16 runs already passed without it. With Sonnet 5.5 the
      share went from 5/8 to 7/8, within noise at that sample size
      ([E1b](preparing-a-repository-evidence.md#e1b)).
- **Installed where the agent runs.** A guidance file told agents to run
  `pre-commit run --all-files`, but pre-commit was not installed in the agent
  image, so the command could never work. Check every command in the image or dev
  container the agent uses, offline if the agent runs offline.
- **Short output and a meaningful exit code.** Agents read the output. A check that
  prints thousands of lines or always exits 0 is not used well.

`dokimi repo scan` checks the commands, build labels, paths and links the guidance
names against the tree.

## 2. Name each tool where the agent will read it

A tool the agent is not told about is rarely used.

- A code-navigation server exposed over MCP, with nothing in the guidance, was
  called in 2 of 24 runs with Haiku 5.5 and 1 of 24 with Sonnet 5.5. Scores did not
  change.
- The same server plus one line in the root `AGENTS.md` saying when to use it was
  called in 13 and 16 of 24 runs, and the scores improved in those arms (§5).

([E4](preparing-a-repository-evidence.md#e4)): 12 questions × 2 repetitions = 24 runs per arm, Haiku 5.5 and Sonnet 5.5,
arms none vs tool only vs tool plus guidance line; counts are runs with at least
one tool call.

Write one line per tool: what it answers and when to prefer it over the default.
For example:

```text
- For who-calls, change-impact or subclass questions, call the code-navigation tools
  before grepping: grep matches every function that shares a name.
```

## 3. Keep the guidance true

Agents trust the guidance over the code until it fails them. Every factual claim
in `AGENTS.md` should be checkable against the tree. Defects found this way in a
real repository, corrected in
[uxlfoundation/oneDAL#3869](https://github.com/uxlfoundation/oneDAL/pull/3869):

| Claim in the guidance | What the code had |
|---|---|
| a generator script that produces the pkg-config files | no such script; the build preprocesses a template |
| every algorithm has `backend/{cpu,gpu}` directories | 26 of 31 algorithms had a CPU backend, 20 of 31 a GPU backend |
| never mix the two internal APIs in one file | the backends that call the older API (46 files) convert its error codes to the newer API's exceptions by design; some files hold both APIs' error and pointer types |

- Prefer precise facts (paths, targets, symbols) to generalisations. State the
  exceptions next to a rule.
- Add a line for each trap the code really has. For example: several unrelated
  functions share a short name, so grep over-matches. With Haiku 4.5, such a line
  raised the recall of missed call sites in review from 0.67 to 0.92 (3 of 12 cells improved, in 2 of 4 tasks, none worsened; p = 0.25, too few to separate from noise), and the
  tool it advertised was called in none of the 12 runs ([E3](preparing-a-repository-evidence.md#e3)).
- Guidance coverage alone does not predict where agents struggle. Neither naming a
  module nor writing about it predicted agent errors. Structure did:
    - modules called from many places;
    - modules that bridge two parts of the code base;
    - names shared by many functions.

    Write guidance there first, and confirm with a small probe run.

`dokimi repo scan` reports paths, commands, identifiers and links in the guidance
that no longer match the tree; it cannot check statements like the last two rows
above. `dokimi repo reach` shows which guidance files a harness actually loads
(§7).

## 4. Give C and C++ tools a complete compile database

Indexers, language servers, linters and fast compile checks all start from
`compile_commands.json`.

- **Cover every build variant.** A database generated from the CPU-only build left
  out every SYCL source. The index then missed a large share of the callers
  of a widely used function, and agents that noticed the gap spent their budget
  checking those files by hand.
- **Generate it from the build system,** so it follows the code: the Bazel and
  CMake exporters, or a dry run of make.
- **Prebuild indexes.** A language-server tool started cold on a 4,000-file repo
  returned no references inside an agent run. A static index built ahead of time
  takes a minute or two per commit and can be cached by commit SHA.
- **Use a compiler-based index for C++.**
    - Tree-sitter call graphs agreed poorly with the compiler-resolved callers.
    - Three compiler front-ends (CodeQL, scip-clang, clangd) agreed closely with
      each other, each scored against the other two.
    - clangd alone drops many template call sites; its union with scip-clang covers
      them.

## 5. When code navigation tools help

A compiler-resolved call graph exposed as tools (who calls, callees, subclasses,
files that change together, likely tests) paid off for questions that enumerate
code. On "who calls X" questions with many callers or ambiguous names:

| Model | F1 without | F1 with tool and guidance line | Cost without | Cost with tool and guidance line |
|---|---|---|---|---|
| Sonnet 5.5 | 0.906 | 0.968 | $0.136 | $0.074 per run |
| Haiku 5.5 | 0.874 | 0.951 | $0.0121 | $0.0081 per run |

[E4](preparing-a-repository-evidence.md#e4): 12 questions × 2 repetitions = 24 runs per arm, Haiku 5.5 and Sonnet 5.5.
F1 is computed on the set of files that call the function, from precision and recall.

On the fix and review tasks measured with Sonnet 5, the guidance line was
present and the tool was called in 0 of 20 runs ([E5](preparing-a-repository-evidence.md#e5)).
The tasks were five replays of real review PRs (`review_replay`), one seeded
missed-caller review (`review_seeded_fc`) and four fix or feature tasks. This
was not repeated on the 5.5 models. Offer it when
the repository has many cross-module call sites or shared names, and measure on
your own tasks.

## 6. Review rules learned from past reviews

Recurring review comments are review knowledge a repository already has.

- **Mine them from history.** Take inline comments that the PR author then
  addressed, keep issues raised in at least two PRs, and scope each rule to the
  paths it applies to.
- **Measure what the guidance would have caught.** `dokimi repo tasks replay`
  turns the review threads of merged PRs into `dokimi:review_replay` tasks
  ([contract §4.6](repo-evaluation-contract.md#46-replay-tasks-from-review-threads)).
  Run them with `guidance:none` and `guidance:raw` and compare
  `recall_addressed`, instead of counting by hand which comments the guidance
  covers.
- **A maintainer curates the list.** In one run, one mined rule contradicted the
  repository's own guidance and had to be dropped.
- **Place them in the prompt for now.** E6 measured rules in the prompt; the
  `rulesmd` arm tested rules in `AGENTS.md` but only on real-PR replays (E7),
  where the effect was zero anyway. `AGENTS.md` placement has not been tested on
  seeded defects.
- **Expect help on recurring defects, not one-offs.**
    - On review tasks with seeded recurring defects, rules raised the recall of
      Codex with a small OpenAI model (`gpt-6-luna`) from 0.68 to 0.875
      (5 of 8 cells improved, in 3 of 4 tasks, none worsened; p = 0.062)
      ([E6](preparing-a-repository-evidence.md#e6)). The defects were drawn from the rules, so this is an upper
      bound.
    - Current Claude models were already near that level without them: Haiku 5.5
      went from 0.84 to 0.875, and Sonnet 5.5 stayed at 0.875.
    - On replays of real PRs, whose comments were almost all specific to that PR,
      the rules changed nothing ([E7](preparing-a-repository-evidence.md#e7)).
- **Linters on the changed lines** (clang-tidy, cppcheck) given to the reviewer as
  hints raised recall on seeded defects for Sonnet 5 (0.750 to 0.875) and
  Haiku 4.5 (0.781 to 0.844) ([E6](preparing-a-repository-evidence.md#e6)). Codex
  with `gpt-6-luna` is the model that lost recall (0.677 to 0.604); its gain came
  from `rules` (0.875). On real PRs they changed nothing
  ([E7](preparing-a-repository-evidence.md#e7)).

## 7. Know which files each harness loads

| Harness | Loads |
|---|---|
| Claude Code 2.1.286 (measured 2026-10) | `CLAUDE.md`. `AGENTS.md` only when no `CLAUDE.md` is in its start-up chain. Subdirectory files lazily, when it reads a file there (a subdirectory `AGENTS.md` only if no `CLAUDE.md` is in the chain). |

Other harnesses (Codex, Copilot and others) are not listed because no loading
behavior was measured for them. Each needs its own `harness_profile.v1` and a
measurement with `dokimi repo reach`; the `text`-trace limit below applies.

Put a `CLAUDE.md` containing `@AGENTS.md` next to each `AGENTS.md`, including in
subdirectories; Claude Code reads no `AGENTS.md` beside a `CLAUDE.md`. Gemini CLI
reads `GEMINI.md` unless `context.fileName` in its settings lists `AGENTS.md`. The
`guidance:bridge` arm (for profiles with a bridge key) writes a `CLAUDE.md`
containing `@AGENTS.md` next to each matched `AGENTS.md` (per the manifest's
`guidance.globs`) where no `CLAUDE.md` exists yet, and can measure this setup's
effect. Targets skipped because a `CLAUDE.md` already exists are listed as
`bridge_skipped` in `meta.json`; `repo report` does not show them.

Loading changes between versions. Run `dokimi repo reach` for the harness and
version you use instead of relying on this table. Only the `claude-code` profile is
built in; for Codex or Copilot, pass a `harness_profile.v1` file with `--harness`
([contract](repo-evaluation-contract.md)). Tool-use counts need a trace the profile
can convert, which today means `claude-stream-json`; a `text` trace gives no
events, turns or cost, so for such a harness `repo reach` can report a file as
loaded but never as fetched by the agent. For Claude Code it requires the `claude`
CLI, credentials and a mandatory `--model` flag; `repo --help` notes it costs one
cheap-model call per scenario.

## 8. Measure each change, and measure again when the model changes

What helps depends on the model:

- With Haiku 4.5, the `tools` arm (named `fmt` and `check` commands plus one `AGENTS.md`
  line, not isolated to compile checks) cut new build breaks in a cross-module rename
  from 5.9 to 0.4 per run; pass rate 4/9 to 6/9 ([E2](preparing-a-repository-evidence.md#e2)).
  The change includes the formatter, so the compile-check effect alone is not known.
  Both figures are pooled over the 3 renames, and no per-task count is stored, so one
  rename can carry the whole drop. The pass rate moved by 2 runs of 9: even as 2 up
  and 0 down, that is p = 0.5.
- With Haiku 5.5 and Sonnet 5.5, both arms passed all 9 runs.

`dokimi repo run` requires the [contract's](repo-evaluation-contract.md) manifest
(with `eval_dir`, `tasks`, `entry_points`, graders and a container image), docker,
and `--agent-network` (mandatory while the egress proxy is not implemented, see
[Running](repo-evaluation-contract.md#9-running)); it refuses a guidance-only manifest.

Treat each item above as a hypothesis for your repository.

1. Write the tasks the repository cares about, with deterministic grading
   ([contract](repo-evaluation-contract.md)).
2. Run the baseline and the arm with `dokimi repo run`, on the current models you
   use. Today an arm can remove or keep the guidance (`guidance:none`,
   `guidance:raw`), or inject a file (`guidance:bridge`, which writes a `CLAUDE.md`
   containing `@AGENTS.md` next to each `AGENTS.md` for profiles with a bridge key).
   Testing an added tool, other than a skill (`skill:`, `skill-catalog:`), needs planned arms. Today, test one added guidance line
   with two task directories that differ only in a `setup_patches` entry (same
   prompt, same graders, distinct ids), or in their `base` commit (see
   [workspace preparation](repo-evaluation-contract.md#workspace-preparation-done-by-the-framework)).
   Run both under `guidance:raw` so that the patch is not removed, and compare
   their rows in `dokimi repo report`. `repo compare` pairs the same task across
   arms, so it cannot pair these two tasks. With 8 cells per row, 5/8 against
   7/8 is within noise: treat a gap as real only when the two
   Wilson intervals in `repo report` do not overlap, or raise the cell count. A patch or a second `base` changes only the
   workspace tree: one image serves every task, so a tool or binary (a pinned
   formatter, a navigation server) has to be in the image already. With the
   `claude-code` profile, which passes `--strict-mcp-config`, an MCP server is
   loaded only from the `.mcp.json` at the workspace root, and only with
   `--tools all` (the profile's `args_if_present` rule: the file must exist in the
   workspace, and `read` and `none` never pass `--mcp-config`). A patch that adds
   or edits that file therefore adds the server in `all` mode; `repo run --tools all`
   refuses a task base whose `.mcp.json` is malformed before any cell starts.
3. Look at whether the agent used the change, not only at the score. A guidance
   line can move the score without the tool it names being called; tool use tells
   you whether the tool itself mattered. An arm in which the tool was never called
   still helps measure guidance-only impact.
4. Keep the change if it helps, and repeat when you move to a new model.

## Checklist

- Every command in the guidance runs in the agent's environment and matches the CI version.
- One command each for format, build-what-changed, test-what-changed and lint.
- Each tool has one line in `AGENTS.md` saying when to use it.
- `dokimi repo scan` reports no failing claims.
- Traps (shared names, cross-module exceptions) are written down.
- C/C++: `compile_commands.json` covers every build variant; indexes are prebuilt if you offer navigation tools.
- Recurring review rules, curated, are in the review prompt (§6); `AGENTS.md` placement is not measured yet.
- A `CLAUDE.md` containing `@AGENTS.md` sits next to each `AGENTS.md` (§7).
- `dokimi repo reach` (only `claude-code` is built in; others need a `harness_profile.v1` file) confirms each harness you use loads the files you rely on.
- Each change was compared against the baseline on current models.
