# Preparing a repository for coding agents

This page is for repository owners who want coding agents (Claude Code, Codex,
Copilot and others) to build, test, change and review their code reliably. It
lists what to put in the repository and its environment, and how to check with
Dokimi that each piece helps.

Every tool and file below is an ordinary repository artifact that people and CI
use too. Nothing is declared for Dokimi. Dokimi only compares configurations:
the same tasks run with and without a change (an arm), and the results are paired
by task. See [repository evaluation](repo-evaluation-contract.md).

The recommendations come from measurements on two C++ libraries (oneDAL and
ScalableVectorSearch) with several model generations. Samples were small (8 to 24
cells per arm) and some effects disappeared when the model changed, so measure
before you rely on any of them (§8).

## 1. Make every command in the guidance runnable

An agent follows the commands the guidance gives. A command that cannot run costs
turns and leads to work-arounds.

- **One command per action.** Format, build what changed, test what changed, lint.
  Prefer the build system and the repository's existing hooks (Bazel `rdeps`
  queries, CMake presets, pre-commit) over new wrapper scripts. CI, people and
  agents then run the same thing.
- **The same version as CI.** If CI pins clang-format 20.1.8 and the environment
  has 18, every formatted file can fail the CI check. In one measurement, giving
  the agent the pinned formatter behind a named command raised the share of runs
  that passed the CI format check from 5/16 to 12/16. Installing the right version
  without naming it raised it only to 7/16.
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

- In one measurement, a code-navigation server exposed over MCP, with nothing in
  the guidance, was called in 0 to 2 of 24 runs.
- The same server plus one line in the root `AGENTS.md` saying when to use it was
  called in 13 to 16 of 24 runs. Only then did it change results.

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
| never mix the two internal APIs in one file | 46 bridge files mix them by design |

- Prefer precise facts (paths, targets, symbols) to generalisations. State the
  exceptions next to a rule.
- Add a line for each trap the code really has. For example: several unrelated
  functions share a short name, so grep over-matches. With one such line, one model
  stopped reporting same-named functions as missed call sites.
- Guidance coverage alone does not predict where agents struggle. Neither naming a
  module nor writing about it predicted agent errors. Structure did:
  - modules called from many places;
  - modules that bridge two parts of the code base;
  - names shared by many functions.

  Write guidance there first, and confirm with a small probe run.

`dokimi repo scan` reports claims that no longer match the tree. `dokimi repo
reach` shows which guidance files a harness actually loads (§7).

## 4. Give C and C++ tools a complete compile database

Indexers, language servers, linters and fast compile checks all start from
`compile_commands.json`.

- **Cover every build variant.** A database generated from the CPU-only build left
  out every SYCL source. The index then missed about a third of the callers of a
  widely used function, and agents that noticed the gap spent their budget checking
  those files by hand.
- **Generate it from the build system,** so it follows the code: the Bazel and
  CMake exporters, or a dry run of make.
- **Prebuild indexes.** A language-server tool started cold on a 4,000-file repo
  returned no references inside an agent run. A static index built ahead of time
  takes a minute or two per commit and can be cached by commit SHA.
- **Use a compiler-based index for C++.** Tree-sitter call graphs found well under
  half the callers. clangd and scip-clang agreed with each other at F1 0.82 to 0.91.
  Their union covers template call sites that either one alone drops.

## 5. When code navigation tools help

A compiler-resolved call graph exposed as tools (who calls, callees, subclasses,
files that change together, likely tests) paid off for questions that enumerate
code. On "who calls X" questions with many callers or ambiguous names:

| Model | Without | With tool and guidance line |
|---|---|---|
| Sonnet 5.5 | 0.906 | 0.968, at half the cost per run |
| Haiku 5.5 | 0.874 | 0.951 |

On the fix and review tasks measured, agents did not call it and results did not
change. Offer it when the repository has many cross-module call sites or shared
names, and measure on your own tasks.

## 6. Review rules learned from past reviews

Recurring review comments are the most transferable review knowledge a repository
has.

- **Mine them from history.** Take inline comments that the PR author then
  addressed, keep issues raised in at least two PRs, and scope each rule to the
  paths it applies to.
- **A maintainer curates the list.** In one run, one mined rule contradicted the
  repository's own guidance and had to be dropped.
- **Keep them where every harness reads them,** in a review section of the root
  `AGENTS.md`.
- **Expect help on recurring defects, not one-offs.**
  - On review tasks with seeded recurring defects, rules raised Codex
    (gpt-6-luna) recall from 0.68 to 0.875.
  - Current Claude models were already near that level without them.
  - On replays of real PRs, whose comments were almost all specific to that PR,
    the rules matched 1 to 2 of 44 comments and changed nothing.
- **Linters on the changed lines** (clang-tidy, cppcheck) given to the reviewer as
  hints helped in the same way, and distracted one weaker model.

## 7. Know which files each harness loads

| Harness (version measured) | Loads |
|---|---|
| Claude Code 2.1.286 | `CLAUDE.md`. `AGENTS.md` only when no `CLAUDE.md` is in its start-up chain. Subdirectory files lazily, when it reads a file there. |
| Codex CLI | the root `AGENTS.md`; subdirectory files on demand; `AGENTS.override.md` replaces the root |
| Copilot CLI | `AGENTS.md`, `.github/copilot-instructions.md`, `CLAUDE.md`, `GEMINI.md` and path-scoped instruction files; not `REVIEW.md` |

Loading changes between versions. Run `dokimi repo reach` for the harness and
version you use instead of relying on this table.

## 8. Measure each change, and measure again when the model changes

What helps depends on the model:
- A fast compile check of the edited files cut new build breaks in a cross-module
  rename from 5.9 to 0.4 per run on one model generation.
- On the next generation, both arms passed every run.

Treat each item above as a hypothesis for your repository.

1. Write the tasks the repository cares about, with deterministic grading
   ([contract](repo-evaluation-contract.md)).
2. Run the baseline and the arm that adds the change (a guidance line, a tool, a
   rules section) with `dokimi repo run`, on the current models you use.
3. Look at whether the agent used the change, not only at the score. An arm in which
   the tool was never called measures the baseline again.
4. Keep the change if it helps, and repeat when you move to a new model.

## Checklist

- [ ] Every command in the guidance runs in the agent's environment and matches
      the CI version.
- [ ] One command each for format, build-what-changed, test-what-changed and lint.
- [ ] Each tool has one line in `AGENTS.md` saying when to use it.
- [ ] `dokimi repo scan` reports no failing claims.
- [ ] Traps (shared names, bridge exceptions) are written down.
- [ ] C/C++: `compile_commands.json` covers every build variant; indexes are
      prebuilt if you offer navigation tools.
- [ ] Recurring review rules, curated, are in the review section of `AGENTS.md`.
- [ ] `dokimi repo reach` confirms each harness you use loads the files you rely on.
- [ ] Each change was compared against the baseline on current models.
