# Contributing

Thanks for your interest in improving dokimi. This guide covers the
mechanics; for architecture and design context start with
[docs/architecture.md](docs/architecture.md).

## License of contributions

By submitting a contribution you agree that it is licensed under the
[Apache License 2.0](../LICENSE), the same license as the project.

## Development setup

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"        # editable install + dev tooling
```

The package exposes a console entry point:

```bash
dokimi --help           # equivalent to `python -m dokimi`
```

Optional feature sets are available as extras: `ocr` (Docling ingestion),
`mcp`, and `embeddings`. Install what you need, e.g. `pip install -e ".[dev,ocr]"`.

## Before you open a PR

From the repo root, `make dev` builds `.venv` with CI's Python, the lock and
every CI tool; `make ci-local` then runs every PR workflow step (read from
`.github/workflows/`, so it cannot drift) and prints a pass/fail/skip table.
`make hooks` adds a pre-push hook with the fast offline subset. By hand,
the same checks CI runs are:

```bash
ruff check .                              # lint
mypy                                      # type check (config in pyproject.toml)
python -m dokimi.schema_check benchmarks/spec.v1.yaml     # validate the benchmark spec
python -m dokimi.config_check             # registry drift check
pytest                                    # tests (offline: connecting off loopback fails the test)
```

CI additionally holds a PR to:

- **Coverage of changed lines ≥ 80%** (when 20+ lines are measurable) —
  `diff-cover coverage.xml --compare-branch=<remote>/main` after
  `pytest --cov=dokimi --cov-branch --cov-report=xml`, where `<remote>` is the
  remote pointing at `intel/dokimi` (`origin` in CI). `make ci-local` finds
  that remote by URL and prints the ref it compares against; pick another with
  `CI_LOCAL_ARGS='--remote <name>'`.
- **mypy on the whole package.** Modules listed under `[[tool.mypy.overrides]]` in
  `pyproject.toml` are exempt until fixed; do not add to that list.
- **Docs build** — `mkdocs build --strict`, from `dokimi/`. `make dev` already
  installs its packages and `make ci-local` runs it; by hand, install them with
  `pip install -r requirements.lock.txt -r requirements-docs.lock.txt`. It fails on
  a link or `#anchor` between pages that does not resolve, on a link into the
  source tree whose file does not exist (an anchor into such a file is not
  checked), and on a list that nests differently on the site than on GitHub
  (Python-Markdown wants nested items indented 4 spaces and a blank line before
  a list). A new page goes in two places: the `docs/README.md` index
  (`tests/test_docs_links.py` checks it) and `nav:` in `mkdocs.yml` (the
  build fails on a page missing from it). `mkdocs serve` previews the site.
- **Workflow lint** — `zizmor --offline --min-severity medium .github/workflows` when you
  edit a workflow.

Dependencies install from `requirements.lock.txt`, and the docs build's from
`requirements-docs.lock.txt` on top of it. When you change a range in
`requirements*.txt`, regenerate the lock (command in its header); the docs lock is
constrained to the main one, so regenerate it after the main lock changes too.

Guidelines:

- **Open PRs from a fork.** Push your branch to your fork of `intel/dokimi`
  and open the PR against `main`. Don't push branches to `intel/dokimi`
  itself, even if you have write access. The rule is for contributors and
  agents; bot update branches (`dependabot/*`, `renovate/*`) are the
  exception. From a clone of `intel/dokimi`:

  ```bash
  gh repo fork --remote --remote-name fork   # adds the fork as `fork`; origin stays intel/dokimi
  git push -u fork <branch>
  gh pr create -R intel/dokimi --base main --head <you>:<branch>
  ```

  Without `--remote-name fork`, `gh repo fork --remote` renames `origin` to
  `upstream` and makes the fork `origin`, so `origin/main` is the fork's
  `main`, not `intel/dokimi`'s.
- **Base every PR on `main`.** Don't stack PRs on each other's branches; if a
  change depends on an open PR, wait for it to merge or combine the two.
- **Keep changes focused.** One logical change per PR; it makes review and
  bisecting easier.
- **Add tests** for behavioral changes. The suite lives in `tests/` and runs
  fully offline (LLM/network calls are mocked).
- **Update docs** in the same PR when you change behavior. Stale docs are
  treated as bugs — see the docs-drift checklist below.
- **Don't commit ad-hoc run output.** Generated artifacts belong under
  `reports/`/`results/` (git-ignored). Only commit curated fixtures that are
  part of a reproducible benchmark.

## Adding things

- A new static metric: [docs/contributing-metric.md](docs/contributing-metric.md)
- A new doc source: [docs/adding-doc-source.md](docs/adding-doc-source.md)
- A terminal-bench task: [docs/contributing-terminal-bench-task.md](docs/contributing-terminal-bench-task.md)

## Docs-drift checklist

When your change touches user-facing behavior, confirm:

- [ ] Quickstart / README commands still match the CLI.
- [ ] Any new flag or artifact field is documented.
- [ ] Code examples in docs still run.
- [ ] Links resolve.

## Subsystem ownership

The repo spans three loosely-coupled subsystems; see
[CODEOWNERS](../.github/CODEOWNERS) for review routing:

- **static benchmark** — `dokimi/runner`, `metrics`, `ingest`, `gate`
- **LLM eval** — `dokimi/eval`, `questions`, `personas`, `treatments`
- **executable tasks** — `dokimi/harnesses`. The task definitions
  themselves are evaluation content and are contributed to the external content
  repository; the format they must follow is specified in
  [docs/contributing-terminal-bench-task.md](docs/contributing-terminal-bench-task.md).
