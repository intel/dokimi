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
  `diff-cover coverage.xml --compare-branch=origin/main` after
  `pytest --cov=dokimi --cov-branch --cov-report=xml`.
- **mypy on the whole package.** Modules listed under `[[tool.mypy.overrides]]` in
  `pyproject.toml` are exempt until fixed; do not add to that list.
- **Workflow lint** — `zizmor --offline --min-severity medium .github/workflows` when you
  edit a workflow.

Dependencies install from `requirements.lock.txt`. When you change a range in
`requirements*.txt`, regenerate the lock (command in its header). Renovate
(`.github/renovate.json5`) proposes updates weekly: one PR for every minor and
patch bump, one per major.

Guidelines:

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
