### Added

- **User outcome contracts** — `pytest --outcomes` checks each promise in
  `tests/outcomes/*.json` (the doc that states it, the code that keeps it, and
  evidence ids) against the tests that ran. A test claims an id with
  `@pytest.mark.outcome("<id>")`. An id no test claims is an error, a claimed
  test that fails fails the verdict, and a claimed test that is skipped makes it
  inconclusive (exit 3). The verdict is written to `outcomes-result.json`
  (`--outcomes-report=<path>` moves it). CI runs the tests with `--outcomes` and
  uploads the file with the other test artifacts. The first contract covers
  pinned question sets.

### Changed

- **`--questions-root` pins the question set** — when `<target>_golden.json`
  exists in the root, `benchmark run` and `evaluate` answer exactly that set and
  skip question generation, where they used to merge it into generated
  questions. Counts and `question_set_hash` of such runs change. A cached
  `questions/<target>.json` in the output directory still wins until
  `--force-regen`; a cached set that differs from the golden set now logs a
  warning with both hashes.
- **`benchmark preflight --questions-root` validates the golden set** — a
  missing root, a missing or empty golden file, or an unreadable or malformed
  one exits 1 and the check prints the golden file path and item count.
- **`evaluate` skips persona discovery for pinned runs** — with a golden file or
  `--questions-from`, personas are not discovered (even with `--force-regen`),
  and `evaluate` no longer prints a `Personas:` path for such a run.
