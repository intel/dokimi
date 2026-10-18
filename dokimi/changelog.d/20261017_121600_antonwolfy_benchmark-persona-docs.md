### Documentation

- **`--force-regen` help and the pipeline docs say when personas are discovered** — `benchmark run --force-regen` and `benchmark batch --force-regen` no longer promise to regenerate personas: a run with a golden set under `--questions-root`, or with `--questions-from`, discovers none, and `--force-regen` re-reads the golden set. `README.md` and `docs/methodology.md` now say the same.
