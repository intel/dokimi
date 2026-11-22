### Changed

- **`skill novelty analyze --estimate-only` prints the same output from `novelty_budget.py`** — the estimate-only branch moved out of `commands/novelty.py`, which the last merges had left at 801 lines (cap 800). The JSON, the extrapolation note and the hints on stderr are unchanged.
