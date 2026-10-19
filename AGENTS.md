# Agent guide

This repo is a measurement framework: it measures whether documentation, a
skill, an MCP doc source, or an agent persona prompt improves a coding agent's
answers and task outcomes. The content it measures (skills, executable tasks,
golden question sets) lives in content repositories and is passed in by path:
`skill:<path>`, `--tasks-root`, `--questions`.

All code, tests and CI live in the `dokimi/` project directory; run commands
from there. The importable package is `dokimi` (`dokimi/dokimi/`); the CLI is
`python cli.py --help`.

## Done means `make ci-local` is green

```bash
make dev        # once, repo root: .venv with CI's Python, the lock, every CI tool
make ci-local   # every PR CI step, read from .github/workflows/; pass/fail/skip table
make ci-local CI_LOCAL_ARGS='--offline --jobs static,typecheck'   # fast inner loop
```

A change is ready for a PR when no row of the `make ci-local` table is `FAIL`.
`ci-local` diffs against the remote whose URL is `intel/dokimi`, whatever it is
called (`CI_LOCAL_ARGS='--remote <name>'` overrides). The full `pytest` run takes
2–3 minutes, longer than a default 2-minute command timeout: give it a 10-minute
timeout, and run the test files you touched first. Beyond lint and tests, CI
also holds a PR to:

- 80% coverage of changed lines (when 20+ are measurable): add tests with the change.
- mypy on the whole package; the `[[tool.mypy.overrides]]` list in
  `pyproject.toml` only shrinks.
- No growth: a file over 800 lines (tests 1000) or a function over complexity 10
  fails unless `scripts/growth_baseline.json` holds it, and then it may only
  shrink. Split instead; after shrinking, `python scripts/check_growth.py --update`.
- Docs that name a command, flag, path or module must match the tree
  (`tests/test_docs_code_refs.py`): rename in code, rename in docs.
- Tests run offline: a connection off loopback fails the test, so mock LLM and
  network calls.
- User outcomes (`tests/outcomes/*.json`, checked by `pytest --outcomes`): a
  test marked `@pytest.mark.outcome(...)` is evidence for a promise in the docs;
  weaken or drop it only together with the doc and the contract.

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
- Anything else: the index, `dokimi/docs/README.md`. A new doc page also goes
  in the `nav:` of `dokimi/mkdocs.yml`, the published site.

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
  `artifacts.SCHEMA_VERSIONS` at it, list the old one in `SUPERSEDED_VERSIONS`.
  CI's `schema_compat` job checks this against the PR base.
- Answer and eval JSON name the two answer conditions `with_docs` and
  `without_docs`, and every stored run uses them: writers, readers, schemas
  and data files keep those keys. Text people read (report headings, console
  output) may label the conditions however it reads best.

## What goes in the tree

- Runs write to git-ignored output dirs under `dokimi/`: `results/`,
  `reports/`, `answers/`, `eval/` (not the `dokimi/dokimi/eval/` package),
  `baselines/`. Logs, coverage files and tarballs stay local too.
- `dokimi/data/` holds only the fixtures this repo's own tests and
  examples read: `data/skills/fastcontext/`, `data/agent_profiles/`,
  `data/explore_tasks/`. Question sets, product skills and tasks belong in
  their content repository.
- A file the package reads at run time goes inside `dokimi/dokimi/` and into
  `[tool.setuptools.package-data]`, or the installed wheel goes without it.
- Design rationale, status and plans go in the PR description or an issue;
  the tree has no ADRs or status reports.
- A new file starts with the SPDX header (`Copyright (C) <year> Intel
  Corporation` / `SPDX-License-Identifier: Apache-2.0`) in its comment syntax.
  CI's license header step (static job) checks it and `make ci-local` does
  not: exemptions and the local command are in `dokimi/CONTRIBUTING.md`.

## Commits and pull requests

- Commit with `git commit -s`: the repo is public and takes DCO sign-off
  (`CONTRIBUTING.md` at the root).
- A PR that changes `dokimi/dokimi/`, `dokimi/data/` or the `[project]` /
  `[tool.setuptools]` tables of `dokimi/pyproject.toml` adds a changelog fragment under
  `dokimi/changelog.d/` (`scriv create`), never an edit to `CHANGELOG.md`; the
  `changelog` workflow enforces it. Versioning and releases:
  `dokimi/docs/releasing.md`.
- Every change to `main` is a PR opened from a fork, based on `main`. Add the
  fork as its own remote so `origin` keeps pointing at `intel/dokimi`:
  `gh repo fork --remote --remote-name fork`, then `git push -u fork <branch>`
  and `gh pr create -R intel/dokimi --base main --head <you>:<branch>`. Push
  branches only to the fork, even with write access to `intel/dokimi` (bot
  branches, `dependabot/*` and `renovate/*`, are the exception).
- When a change depends on an open PR, wait for it to merge or put both in one
  PR; PRs are not stacked on each other's branches.
