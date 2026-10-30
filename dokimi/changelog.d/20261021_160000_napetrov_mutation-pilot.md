### Added

- **Mutation pilot over the measurement core** — `.github/workflows/mutation.yml`
  runs mutmut 3.8.0 (hash-pinned in its own `requirements-mutation.lock.txt`, installed by that workflow alone) weekly and on demand over
  `eval/stats.py`, `eval/binary_stats.py`, the score, regression, critical-band and
  trust gates and `caching.py`, and writes the score per module and the surviving
  mutants to the job summary. It never fails a build. New tests kill the survivors
  that were real gaps (inclusive thresholds, the Wilcoxon veto, per-condition metric
  keys, the bootstrap tail index, the cache key's role and sampling parameters), which
  takes the pilot from 75.3% to 92.0% of mutants killed. Configuration:
  `[tool.mutmut]` in `pyproject.toml`; usage: CONTRIBUTING.md.

### Changed

- **`run --strict` reads the critical bands strictly** — a NaN metric now violates its band; it passed silently before. A band the gate cannot read (an unknown condition, or a `None` metric) exits 2 and names the spec, instead of ending in a traceback.
