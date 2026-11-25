# Agent guide

This repo is dokimi, A/B testing for agent context: it measures whether documentation, a
skill, an MCP doc source, or an agent persona prompt improves a coding agent's
answers and task outcomes. The content it measures (skills, executable tasks,
golden question sets) lives in content repositories and is passed in by path:
`skill:<path>`, `--tasks-root`, `--questions`.

Run package commands from `dokimi/`. The importable package is
`dokimi/dokimi/` relative to the root. Repository self-evals live in
[`.agent-eval/`](.agent-eval/README.md) and workflows in `.github/workflows/`.
For PR review, open [REVIEW.md](REVIEW.md) and scan the full diff before
investigating individual findings.

## Done means `make ci-local` is green

```bash
make dev        # once, from the repo root
cd dokimi
../.venv/bin/python cli.py --help
make ci-local   # runs local equivalents of PR checks and reports skips
make ci-local CI_LOCAL_ARGS='--offline --jobs static,typecheck'   # fast inner loop
```

On a historical self-eval task, use the Python installed in the eval image if
`.venv` is unavailable. Run `python -m pytest` from `dokimi/` and check the
selected revision for a helper before invoking it.

Run touched tests first with `../.venv/bin/python -m pytest` and allow a
10-minute timeout for the full suite. Before opening a PR, resolve every
`FAIL` and confirm the relevant checks ran. Report any relevant skips.
If you pipe a check through `head`, `tail` or `tee`, enable `set -o pipefail`
so the check's failure reaches the shell. See [CONTRIBUTING.md](dokimi/CONTRIBUTING.md)
for local coverage and skips. `ci-local` finds the remote pointing to
`intel/dokimi` by URL (`CI_LOCAL_ARGS='--remote <name>'` overrides it).
CI also holds a PR to:

- 80% coverage of changed lines (when 20+ are measurable): add tests with the change.
- mypy on the whole package, no module exempt: fix a type error rather than
  adding an `ignore_errors` override to `pyproject.toml`.
- Imports follow the layers in `dokimi/tests/test_import_architecture.py`: no
  upward import, no new cycle, LLM SDKs only in `dokimi.llm`, spawns only in
  `dokimi.process`. Its baselines only shrink.
- No growth without a rationale: split files over 800 lines (tests 1000) or
  functions over complexity 10. Lower recorded baselines after shrinking with
  `python scripts/check_growth.py --update`. See the contributing guide for
  the rationale required to add or raise a baseline entry.
- Extract shared code instead of copying it. `scripts/check_duplicates.py`
  detects clones of 40+ AST nodes even when locals are renamed.
- Tests assert observable behavior. Mock network and LLM calls. Wait on
  conditions with `tests/waiting.py` and measure complexity by counting work
  or using `tests/growth_timing.py`. See `scripts/check_test_timing.py` for
  `# timing-ok: <reason>` and `scripts/check_test_narrowing.py` for
  `Test-narrowed: <reason>` when reducing existing coverage.
- Keep touched comment blocks to two lines and link to any backstory.
  Catch expected exceptions, log unexpected ones with their traceback, or
  re-raise. Explain a necessary broad catch in `# noqa: BLE001 - <reason>`.
- Keep outcome tests and their documented promises in sync. `pytest --outcomes`
  checks `tests/outcomes/`, including enforced journey criteria that require
  passing `cjm-*` evidence. See [CONTRIBUTING.md](dokimi/CONTRIBUTING.md).

## Before changing a subsystem, read its doc

- Any module, or where new code goes: `dokimi/docs/architecture.md`.
- A static metric: `dokimi/docs/contributing-metric.md`.
- A doc source: `dokimi/docs/adding-doc-source.md`.
- Repository evaluation (`dokimi/dokimi/repos/`, `cli.py repo …`):
  `dokimi/docs/repo-evaluation-contract.md`.
- Skill novelty (`dokimi/dokimi/novelty/`, `cli.py novelty …`):
  `dokimi/docs/skill-novelty.md`.
- Executable tasks (Docker + oracle solution + pytest verifier) or harnesses:
  `dokimi/docs/contributing-terminal-bench-task.md`. Tasks live in a
  content repository; the framework reads a checkout via `--tasks-root`.
- Docs or CLI: update code references when a command, flag, path or module
  changes. Add every new doc page to the index used by
  `dokimi/tests/test_docs_links.py` (`dokimi/docs/documentation-index.md`, or
  `dokimi/docs/README.md` on older branches) and to `nav:` in `dokimi/mkdocs.yml`.
  From `dokimi/`, run
  `../.venv/bin/python -m pytest tests/test_docs_links.py tests/test_docs_code_refs.py`
  to check the page, links and CLI references.

Registries, bundled in the package (`dokimi/dokimi/`) so they ship in the wheel:

- `dokimi/dokimi/products.yaml`: product identity and doc sources.
- `dokimi/dokimi/intents.yaml`: problem/intent domains, and the only
  domain→product mapping.
- `dokimi/dokimi/config/products.yaml`: LLM/runtime config for the eval track;
  its product identity follows `products.yaml` (`config_check` fails on drift).

## Persisted formats

- Every artifact format is a versioned JSON Schema in
  `dokimi/dokimi/schemas/` (`<kind>.vN.json`). Edit a version in place only
  to widen it. A change that would reject an artifact already written under it
  is a new version: add `<kind>.v<N+1>.json`, point
  `artifacts.SCHEMA_VERSIONS` at it, and list the old version in `SUPERSEDED_VERSIONS`.
  Keep the old fixture and add one for the new version under
  `dokimi/tests/fixtures/artifacts/`. Check both with `tests/test_schema_compat.py`
  and verify a normal writer emits the new version. CI's `schema_compat` step
  (`ci.yml`, `static`) checks compatibility against the PR base.
- Answer and eval JSON name the two answer conditions `with_docs` and
  `without_docs`, and every stored run uses them: writers, readers, schemas
  and data files keep those keys. Text people read (report headings, console
  output) may label the conditions however it reads best.
- Code that reads answers, eval or arms records goes through
  `dokimi/dokimi/records.py` (`read`/`load`/`records`), not
  `.get("with_docs")` on the dict: it is the one place that knows the keys
  the writers write. `dokimi/tests/test_records_guard.py` fails a new such read
  and lists the exceptions, which only shrink.

## What goes in the tree

- Runs write to git-ignored output dirs under `dokimi/`: `results/`,
  `reports/`, `answers/`, `eval/` (not the `dokimi/dokimi/eval/` package),
  `baselines/`. Logs, coverage files and tarballs stay local too.
- `dokimi/data/` holds only the fixtures this repo's own tests and
  examples read: `data/skills/fastcontext/`, `data/agent_profiles/`,
  `data/explore_tasks/`. The one other skill there, `data/skills/dokimi-design-evals/`,
  is guidance for using dokimi itself, not a product skill; its links and
  commands are checked by `tests/test_bundled_skills.py`. Question sets,
  product skills and tasks belong in their content repository.
- A file the package reads at run time goes inside `dokimi/dokimi/` and into
  `[tool.setuptools.package-data]`, or the installed wheel goes without it.
- Design rationale, status and plans go in the PR description or an issue;
  the tree has no ADRs or status reports. The one exception is the research
  register, `dokimi/research/records/`: one record per idea, experiment,
  finding, decision or implementation link, negative results included (`dokimi/docs/research-register.md`).
- A new file starts with the SPDX header (`Copyright (C) <year> Intel
  Corporation` / `SPDX-License-Identifier: Apache-2.0`) in its comment syntax.
  CI's license header step (static job) checks it and `make ci-local` does
  not: exemptions and the local command are in `dokimi/CONTRIBUTING.md`.

## Commits and pull requests

- Commit with `git commit -s`: the repo is public and takes DCO sign-off
  (`CONTRIBUTING.md` at the root).
- A PR that changes `dokimi/dokimi/`, `dokimi/data/` or the `[project]` (bar
  `project.version`) / `[tool.setuptools]` tables of `dokimi/pyproject.toml` adds a changelog fragment under
  `dokimi/changelog.d/` (`scriv create`), never an edit to `CHANGELOG.md`; the
  `Fragment for package changes` step in `ci.yml`'s `static` job enforces it. Versioning and releases:
  `dokimi/docs/releasing.md`.
- Every change to `main` is a PR opened from a fork, based on `main`. Add the
  fork as its own remote:
  `gh repo fork --remote --remote-name fork`, then `git push -u fork <branch>`
  and `gh pr create -R intel/dokimi --base main --head <you>:<branch>`. Push
  branches only to the fork, even with write access to `intel/dokimi` (bot
  branches, `dependabot/*` and `renovate/*`, are the exception).
- When a change depends on an open PR, wait for it to merge or put both in one
  PR; PRs are not stacked on each other's branches.

## Before opening a PR

Review your diff against [REVIEW.md](REVIEW.md). For the commands below, set
`base_ref` to the upstream `main` ref named by `make ci-local`, for example
`intel/main` or `origin/main`.

- For each test of changed behavior, break the behavior, confirm the test fails,
  then revert the break. From `dokimi/`, run
  `../.venv/bin/python scripts/mutate_changed.py --base "$base_ref"` to reproduce
  CI's advisory mutation check. Cover meaningful survivors and explain
  equivalent ones in the PR.
- For a bug fix, find every other site with the same bug class. Fix the ones
  within this PR's scope; list the rest in the description or an issue.
- Where it applies, run the changed command on a bad input: missing file,
  empty file, wrong shape, `None` where a value is expected, a Windows path.
  Say in the description what you could not run.
- Trace new error paths and tests to the contract: a new `raise`, `sys.exit`
  or refusal names, in its message, docstring or the PR, the doc, contract or
  issue that asks for it; a test of new behaviour cites the doc line it checks.
  From `dokimi/`, run `../.venv/bin/python scripts/list_new_error_paths.py --base "$base_ref"`.
  It lists a `raise`, a `sys.exit` or `SystemExit` call, or a literal `exit_code=N`
  in the committed PR diff. Check uncommitted edits, `parser.error` and numeric
  returns directly.
- An unclear requirement goes in the PR description's "Open questions", not
  into code that guesses: no error path or test the spec cannot be quoted for.
- Check that the PR title, description, docstrings and changelog fragment say
  what the code does (exit codes especially).
- Re-review your own fix commits during review with the same care.
