### Fixed

- **`runs gc --keep-baselines` refuses a baseline copy that is not a JSON object** — a copy holding `[]` or another non-object used to count as holding no run, so its run could be selected for removal. It now exits 2 naming the copy, as for a copy that cannot be read. `docs/runs.md` also no longer says a questions set from `eval questions generate` has no subject: `runs list` shows its `product`.
