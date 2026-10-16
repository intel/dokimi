### Added

- **User outcome contracts** — test outcomes are now checked by a pytest plugin
  called with `pytest --outcomes`. The plugin verifies that (1) each documented
  outcome has at least one test that claims it, (2) claimed tests pass, and
  (3) no test that claims an outcome is skipped. A file `outcomes-result.json`
  records the result. CI's test step uploads this file as evidence of the
  contract. The plugin runs offline and adds no files outside `tests/`.
- **`pytest.mark.outcome("<id>")` marker** — annotate test functions to tie them
  to specific outcome ids. Pairs with `tests/outcomes/<name>.json`, which lists
  the id refs and documentation links. A test marked with an outcome id is
  required to be in `@pytest.fixture(scope="session")` or not skipped, so users
  can rely on it.

### Changed

- **`--questions-root` now pins the question set** — when a golden set file
  exists in the root (`<target>_golden.json`), `benchmark run` and `evaluate`
  load only that set and skip generation. Before, a fresh run merged the golden
  set into generated questions, so two runs on the same root answered different
  sets. The cached `questions/<target>.json` (if present) still takes
  precedence, but a run with no cache now answers only the golden set, not a
  merged set. Question counts and `question_set_hash` of such runs change.
- **`benchmark preflight --questions-root` now validates the golden set** — if
  the root is missing, has no golden file, or the golden file is unreadable,
  the preflight exits 1 instead of passing. This matches documented behavior
  that `--questions-root` pins the question set.
- **`pipeline.orchestrate()` stores a `skipped` flag in the personas step** —
  when personas generation is skipped (because a golden file or `--questions-from`
  pins the set), the step now records `skipped: true` and omits the `path` key,
  since no file is written. `cli.commands.orchestrate()` now checks this flag
  before printing the personas path to the user.

### Fixed

- **A bad golden file no longer crashes a cached run** — loading and comparing
  the golden set now happens inside a `try` block, so a cached set wins even
  if the golden file is corrupt or has an unexpected structure. The failure is
  logged and the run continues.
- **Golden set edits are now detected** — instead of comparing only the `id`
  sets (which misses edited questions and false negatives when items have no
  `id`), the warning now compares the hash of the cached and golden question
  sets, and logs both hashes (or difference counts) so the user can debug.
