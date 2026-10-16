# Contributing

Thanks for your interest in improving dokimi. This guide covers the
mechanics; for architecture and design context start with
[docs/architecture.md](docs/architecture.md).

## License of contributions

By submitting a contribution you agree that it is licensed under the
[Apache License 2.0](../LICENSE), the same license as the project.

## Development setup

With Python 3.11+:

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
- **Tests that fail loudly.** `pytest` fails a test that runs over 60 s
  (`@pytest.mark.timeout(N)` allows more), turns a warning into an error, and
  shuffles the test order (pytest-randomly). The header prints
  `--randomly-seed=N`; pass it to replay that order, or `-p no:randomly` for file
  order. A third-party warning gets a narrow `filterwarnings` ignore in
  `pyproject.toml` with the reason. The plugins come from `requirements-test.txt`:
  a venv without them stops with `Unknown config option: timeout`, so reinstall.
- **User outcomes hold.** `pytest --outcomes` checks each promise in
  `tests/outcomes/*.json` (what a doc tells the user, the code that keeps it,
  and evidence ids) against the tests that ran. A test claims an id with
  `@pytest.mark.outcome("<id>")`. An id no test claims, or a claimed test that
  fails, fails the job; if every claimed test is skipped, the job is inconclusive.
  `outcomes-result.json` says which. Changing a promised behaviour means changing
  the doc, the test and the contract together. `pytest --outcomes -m outcome` runs
  only the evidence.
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

**pre-commit.** `.pre-commit-config.yaml` at the repo root runs the fast
offline checks on the files a commit stages: `ruff check` (the version and
config CI uses), zizmor on the workflows, YAML/JSON/TOML syntax, conflict
markers and files over 500 KB. From the repo root:

```bash
pip install pre-commit && pre-commit install   # `make dev` already installs pre-commit
pre-commit run --all-files                     # the whole tree, as CI's pre-commit job does
```

mypy and pytest are not hooks: they need the full environment and pytest takes
minutes. `make ci-local` runs both; `make hooks` runs mypy before a push.

Dependencies install from `requirements.lock.txt`, and the docs build's from
`requirements-docs.lock.txt` on top of it. When you change a range in
`requirements*.txt`, regenerate the lock with the command in its header: run it
from `dokimi/`, and add `--upgrade` to pick up new versions (without it, uv
keeps the current pins). The command's `--exclude-newer=P7D` skips releases
less than 7 days old; if a security fix is newer than that, its vulnerability PR
fails to resolve, so regenerate the lock by hand without the flag. The docs lock
is constrained to the main one, so regenerate it (command in its header) after
the main lock changes too.

Renovate (`.github/renovate.json5`) proposes updates on Mondays for releases at
least 7 days old: one grouped PR for all minor and patch updates, one PR per
major, a monthly lock refresh, and security fixes as soon as an advisory
appears. A pre-commit hook rev and the pin `ci.yml` installs for the same tool
(ruff, zizmor) come in one PR of their own. Renovate does not update
`requirements-docs.lock.txt` yet; pip-audit in CI still audits it.

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
