I scanned examples/toy-repo with dokimi's L0 check (no LLM):

    python cli.py repo scan ../examples/toy-repo --manifest repo-eval.yaml

The manifest is saved as examples/toy-repo/repo-eval.yaml (globs `**/AGENTS.md`, also_scan
AGENTS.md and README.md, adapters make and pytest; eval_dir/entry_points/tasks are required
placeholders that a scan does not use). Result: 15 claims, 13 ok, 2 fail.

Wrong statements in AGENTS.md:

1. Line 9: "Expressions are tokenized and parsed in `src/tinycalc/parser.py`" -- that file does
   not exist. The tokenizer and parser are in `src/tinycalc/tokens.py` (`tokenize`, `parse`;
   `evaluate.py` imports from it, and the tests are `tests/test_tokens.py`). Fix: change the path
   to `src/tinycalc/tokens.py`.
2. Line 23: the link to the release checklist points at `docs/releasing.md`, which does not
   exist. The checklist is `docs/release.md` (README.md links it). Fix: change the link to
   `docs/release.md`.

Everything else checks out: cli.py, evaluate.py, tests/test_evaluate.py, tests/test_tokens.py,
the make targets install/test/lint, docs/usage.md, pyproject.toml and __init__.py.
