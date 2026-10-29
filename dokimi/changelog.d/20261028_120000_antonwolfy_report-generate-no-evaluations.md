### Fixed

- **`report generate` exits 1 with a message on an eval file that holds no
  evaluations** — `{}`, `{"evaluations": []}` or `[]` as `--eval` now prints
  `Error: --eval <path> holds no evaluations` and writes no report, instead of
  ending in a `ZeroDivisionError` traceback. This holds for `--format json`
  too, which used to exit 0 and write a report of zero stats. A pipeline run
  that judges no answers now shows `n/a` in the report's failure-analysis Rate
  column instead of failing the same way.
