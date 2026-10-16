# Preparing a repository for coding agents

> **Status: recommendations.** §1–§7 apply today. The measurement loop in §8 runs
> today for guidance arms (`guidance:none`, `guidance:raw`, `guidance:bridge`).
> Arms that add a file or a tool are planned; use `setup_patches` or a distinct
> `base` per configuration (see [workspace preparation](repo-evaluation-contract.md#workspace-preparation-done-by-the-framework)) to test them.

This page is for repository owners who want coding agents (Claude Code, Codex,
Copilot and others) to build, test, change and review their code reliably. It
lists what to put in the repository and its environment, and how to check with
Dokimi that each piece helps. Measurements are marked with versions when available;
rows marked vendor-documented are based on published harness behavior, not measured
here.

Every tool and file below is an ordinary repository artifact that people and CI
use too. No tool is declared to Dokimi. Dokimi compares configurations: the same
tasks run with and without a change (an arm). See
[repository evaluation](repo-evaluation-contract.md).

The recommendations come from measurements on two C++ libraries (oneDAL and
ScalableVectorSearch). Agents were Claude Code with Haiku and Sonnet 5.5, plus
earlier generations where noted, and Codex. Samples were small: 8 to 24 cells per
arm. Some effects disappeared when the model changed, so measure before you rely
on any of them (§8).

## 1. Make every command in the guidance runnable

An agent follows the commands the guidance gives. A command that cannot run costs
turns and leads to work-arounds.

- **One command per action.** Format, build what changed, test what changed, lint.
  Prefer the build system and the repository's existing hooks (Bazel `rdeps`
  queries, CMake presets, pre-commit) over new wrapper scripts. CI, people and
  agents then run the same thing.
- **The same version as CI.** If CI pins clang-format 20.1.8 and the environment
  has 18, every formatted file can fail the CI check.
    - With Haiku 4.5, a named command for the pinned formatter raised the share of
      runs that passed the CI format check from 5/16 to 12/16 (p = 0.032). Installing
      the right version without naming it raised it to 7/16 (p = 0.149 vs 12/16, and
      p = 0.716 vs 5/16; n too small to separate the strategies).
    - With Haiku 5.5, runs already passed 12/16 without it. With Sonnet 5.5 the
      share went from 5/8 to 7/8 (p = 0.569, sample too small to resolve).
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
  called in 13 and 16 of 24 runs, and only then did the scores improve (§5).

Write one line per tool: what it answers and when to prefer it over the default.
For example:

```text
- For who-calls, change-impact or subclass questions, call the code-navigation tools
  before grepping: grep matches every function that shares a name.
```

## 3. Keep the guidance true

Agents trust the guidance over the code until it fails them. Every factual claim
in `AGENTS.md` should be checkable against the tree. Defects found this way in a
real repository:

| Claim in the guidance | What the code had |
|---|---|
| a generator script that produces the pkg-config files | no such script; the build preprocesses a template |
| every algorithm has `backend/{cpu,gpu}` directories | 26 of 31 had a CPU backend, 20 of 31 a GPU backend |
| never mix the two internal APIs in one file | the backends that call the older API (46 files) convert its error codes to the newer API's exceptions by design; 16 files hold both APIs' error and pointer types |

- Prefer precise facts (paths, targets, symbols) to generalisations. State the
  exceptions next to a rule.
- Add a line for each trap the code really has. For example: several unrelated
  functions share a short name, so grep over-matches. With Haiku 4.5, such a line
  raised the recall of missed call sites in review from 0.67 to 0.92, without the
  tool it advertised ever being called.
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
  out every SYCL source. The index then missed about 30% of the callers (59 of
  190) of a widely used function, and agents that noticed the gap spent their budget checking
  those files by hand.
- **Generate it from the build system,** so it follows the code: the Bazel and
  CMake exporters, or a dry run of make.
- **Prebuild indexes.** A language-server tool started cold on a 4,000-file repo
  returned no references inside an agent run. A static index built ahead of time
  takes a minute or two per commit and can be cached by commit SHA.
- **Use a compiler-based index for C++.**
    - Tree-sitter call graphs reached F1 below 0.5 against the compiler-resolved
      callers.
    - Three compiler front-ends (CodeQL, scip-clang, clangd) agreed at F1 0.82 to
      0.91, each scored against the other two.
    - clangd alone drops many template call sites; its union with scip-clang covers
      them.

## 5. When code navigation tools help

A compiler-resolved call graph exposed as tools (who calls, callees, subclasses,
files that change together, likely tests) paid off for questions that enumerate
code. On "who calls X" questions with many callers or ambiguous names:

| Model | Without | With tool and guidance line |
|---|---|---|
| Sonnet 5.5 | 0.906 | 0.968, at half the cost per run |
| Haiku 5.5 | 0.874 | 0.951 |

On the fix and review tasks measured, with Sonnet 5, agents did not call it and
results did not change; this was not repeated on the 5.5 models. Offer it when
the repository has many cross-module call sites or shared names, and measure on
your own tasks.

## 6. Review rules learned from past reviews

Recurring review comments are review knowledge a repository already has.

- **Mine them from history.** Take inline comments that the PR author then
  addressed, keep issues raised in at least two PRs, and scope each rule to the
  paths it applies to.
- **A maintainer curates the list.** In one run, one mined rule contradicted the
  repository's own guidance and had to be dropped.
- **Keep them in a review section of the root `AGENTS.md`,** and check that each
  harness you use loads that file (§7).
- **Expect help on recurring defects, not one-offs.**
    - On review tasks with seeded recurring defects, rules raised the recall of
      Codex with a small OpenAI model (gpt-6-luna) from 0.68 to 0.875.
    - Current Claude models were already near that level without them.
    - On replays of real PRs, whose comments were almost all specific to that PR,
      the rules matched 1 to 2 of 44 comments and changed nothing.
- **Linters on the changed lines** (clang-tidy, cppcheck) given to the reviewer as
  hints helped in the same way, and distracted one weaker model.

## 7. Know which files each harness loads

| Harness | Loads |
|---|---|
| Claude Code 2.1.286 (measured 2026-10) | `CLAUDE.md`. `AGENTS.md` only when no `CLAUDE.md` is in its start-up chain. Subdirectory files lazily, when it reads a file there (a subdirectory `AGENTS.md` only if no `CLAUDE.md` is in the chain). |
| Codex CLI (vendor-documented) | the root `AGENTS.md`; subdirectory files on demand; `AGENTS.override.md` replaces the `AGENTS.md` in its own directory |
| Copilot CLI (vendor-documented) | `AGENTS.md`, `.github/copilot-instructions.md`, `CLAUDE.md`, `GEMINI.md` and path-scoped instruction files; not `REVIEW.md` |

Put a `CLAUDE.md` containing `@AGENTS.md` next to each `AGENTS.md`, including in
subdirectories; Claude Code reads no `AGENTS.md` beside a `CLAUDE.md`. Gemini CLI
reads `GEMINI.md` unless `context.fileName` in its settings lists `AGENTS.md`. The
`guidance:bridge` arm (for profiles with a bridge key) writes a `CLAUDE.md`
containing `@AGENTS.md` and can measure this setup's effect.

Loading changes between versions. Run `dokimi repo reach` for the harness and
version you use instead of relying on this table. Only the `claude-code` profile is
built in; for Codex or Copilot, pass a `harness_profile.v1` file with `--harness`
([contract](repo-evaluation-contract.md)). For Claude Code it requires the `claude`
CLI, credentials and a mandatory `--model` flag; `repo --help` notes it costs one
cheap-model call per scenario.

## 8. Measure each change, and measure again when the model changes

What helps depends on the model:

- With Haiku 4.5, a fast compile check of the edited files cut new build breaks in
  a cross-module rename from 5.9 to 0.4 per run.
- With Haiku 5.5 and Sonnet 5.5, both arms passed every run.

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
   Testing one added line or tool needs planned arms; use `setup_patches` or a
   distinct `base` (see [workspace preparation](repo-evaluation-contract.md#workspace-preparation-done-by-the-framework))
   today.
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
- Traps (shared names, bridge exceptions) are written down.
- C/C++: `compile_commands.json` covers every build variant; indexes are prebuilt if you offer navigation tools.
- Recurring review rules, curated, are in the review section of `AGENTS.md`.
- `dokimi repo reach` (only `claude-code` is built in; others need a `harness_profile.v1` file) confirms each harness you use loads the files you rely on.
- Each change was compared against the baseline on current models.
