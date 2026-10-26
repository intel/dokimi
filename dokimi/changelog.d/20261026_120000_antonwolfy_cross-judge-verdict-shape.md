### Fixed

- **`eval cross-judge` exits 2 on a `--pairwise` or `--checklist` entry of the wrong shape,
  before any judge call** — a verdict with no numeric `score`, an arm that is not an object
  of verdicts, or a checklist question without a `question_id`, an `arms` object or a number
  (or null) `coverage` now prints `Error: <file>: <entry> ...` and exits 2 instead of ending
  in a Python traceback. The entries are read with the file, before `--judges` makes its
  first paid call; until now a wrong shape ended the run after those calls.
