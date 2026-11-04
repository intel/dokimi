### Fixed

- **`eval judge agreement`, `eval score pairwise`, `eval score checklist` and `eval judge wording` exit 2
  on an unreadable input file** — a missing or malformed `--answers`, `--pairwise`, `--checklist`
  or `--validity` file, or one that is not a JSON object (or lacks the `verdicts`,
  `questions` or `summary` section `eval judge agreement` reads from it), now prints
  `Error: ...` naming the file and exits 2 (bad input, not a failed gate)
  instead of ending in a Python traceback.
