# Agent instructions for this repository

This repo is a measurement framework: it evaluates whether documentation, a
skill, an MCP doc source, or an agent persona prompt actually improves a coding
agent's answers and task outcomes.

- `dokimi/` — the framework, a self-contained Python project. All
  runnable code, tests, and CI live here; there is nothing else to build.
- The evaluation content being measured (skills, executable tasks, golden
  question sets) is supplied to the framework by path — `skill:<path>`,
  `--tasks-root`, `--questions` — so it can live wherever it is maintained.

## Working in `dokimi/`

```bash
cd dokimi
pip install -r requirements.txt -r requirements-test.txt
python -m pytest -q                      # run before every PR; CI runs this
python -m dokimi.config_check            # registry consistency; CI runs this
python cli.py --help                     # single CLI entry point
```

CI also runs `ruff check .`, `mypy`, and `python -m dokimi.schema_check`.
See `dokimi/CONTRIBUTING.md` for the full set of checks to run before a PR.
Before pushing: `make dev` once (repo root, builds CI's `.venv`), then `make ci-local` (every PR CI step).

- The Python package is `dokimi/dokimi/` (package inside the project directory),
  organized by pipeline stage (`metrics/`, `questions/`, `eval/`, `gate/`, `treatments/`, …).
  See `dokimi/docs/architecture.md` for the module map.
- Key registries (top level of `dokimi/`):
  - `dokimi/products.yaml` — product identity + doc sources (what products exist).
  - `dokimi/intents.yaml` — problem/intent domains mapped to products (what user
    problems exist). The domain→product mapping lives only here.
  - `dokimi/config/products.yaml` — LLM/runtime config for the eval track only;
    identity stated there must not drift from `products.yaml`.
- Executable tasks (Docker + oracle solution + pytest verifier) are evaluation
  content and live in a separate content repository. Point the framework at a
  checkout with `--tasks-root`; the format is specified in
  `dokimi/docs/contributing-terminal-bench-task.md`. CI proves the
  harnesses against an external catalog, not against in-repo tasks.

## Conventions

- Never commit generated artifacts: `results/`, `reports/`, and anything written
  to `baselines/`, `answers/` or `eval/`, coverage files, logs, tarballs. Only
  curated *inputs* go under `dokimi/data/`, and only the fixtures the
  framework's own tests and examples need
  (`dokimi/data/skills/fastcontext/`, `agent_profiles/`, `explore_tasks/`) — question sets and product skills are
  evaluation content, passed in by path from a content checkout. Captured runs
  stay out of the tree and their formats are locked by
  `dokimi/dokimi/schemas/`.
- Status reports and phase plans do not belong in the repo — use PR
  descriptions and issues.
- All changes to `main` go through pull requests, opened from a fork: push the
  branch to your fork and open the PR against `intel/dokimi` `main`
  (`gh pr create -R intel/dokimi --base main --head <you>:<branch>`). Do not
  push branches to `intel/dokimi`, even with write access. Base every PR on
  `main`; do not stack PRs on each other's branches — if a change depends on
  an open PR, wait for it to merge or put both in one PR.
- Persisted answer/eval JSON keeps legacy `with_docs`/`without_docs` keys for
  the two answer conditions — do not rename them in data files.
- Docs: `dokimi/docs/README.md` is the documentation index; record
  design rationale in PR descriptions and issues, not as in-repo ADRs.
