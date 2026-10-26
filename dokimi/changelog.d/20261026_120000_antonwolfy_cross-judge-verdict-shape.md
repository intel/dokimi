### Fixed

- **`eval cross-judge` exits 2 on a `--pairwise` or `--checklist` entry of the wrong shape,
  before any judge call** — a verdict whose `score` is not a finite number (missing, null,
  a string, a bool, NaN, infinite or past a float's range), an arm that is not an object of
  verdicts, or a checklist question without a `question_id` (or with a null or bool one),
  without an `arms` object, or with a `coverage` that is neither a finite number nor null
  now prints `Error: <file>: <entry> ...` and exits 2 instead of ending in a Python
  traceback or passing a bad value on. The entries are read with the file, before
  `--judges` makes its first paid call; until now a wrong shape ended the run after those
  calls.
