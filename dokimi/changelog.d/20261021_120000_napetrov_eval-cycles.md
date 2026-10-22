### Removed

- **`dokimi.eval.stats` no longer re-exports the interaction tests** — `detect_factorial_cells`, `interaction_test` and `interaction_significance` are imported from `dokimi.eval.interactions`, where they are defined. The re-export made `stats` and `interactions` import each other. Results are unchanged. If your code imports these names from `dokimi.eval.stats`, import them from `dokimi.eval.interactions` instead.
