### Fixed

- **`runs show RUN_ID` lists the run when a file of that name under the root is not an artifact** — a text file `results/20261028T093512Z-77c2e0b4` made the command exit 2 with `is not an artifact` although an artifact had that `run_id`. Only a file that opens with the `schema_version` of a registered kind wins over the run now, as `docs/runs.md` says. `docs/runs.md` also names the `problems` count of a `catalog_eval.v1` receipt and says a non-ISO `generated_at` is printed as written and placed by mtime. (#972)
