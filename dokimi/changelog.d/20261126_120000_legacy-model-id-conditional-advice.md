### Fixed

- **`legacy-model-id` advice is now conditional, and the docs say that retirement and migration text is reported too** — a table row that lists a model as retired, or the old side of a before/after example, is reported, and the advice `use X` would delete the notice or break the example. It now reads `if this text calls the model, use X`. `docs/external-content.md` says these lines are reported and that a catalog documenting model history can use `--ignore legacy-model-id`. (#945)
