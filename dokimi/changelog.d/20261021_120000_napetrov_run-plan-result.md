### Added

- **Run plan and result artifacts** — two new artifact kinds, `plan.v1` and `result.v1`. A plan lists every cell a run will measure (arm × model × task × rep, each with a stable `cell_id`) and records the subjects, suites, models, harnesses, grader digest, budget estimate and the one `varied_variable` (a list for a 2×2 design). A result gives each cell one status: `pass`, `fail`, `inconclusive`, `invalid`, `infra_error` or `not_run`, and refers to its plan by `plan_digest`. `dokimi.runner.plan.completeness` computes planned minus completed cells. A run is `complete` only when every planned cell is `pass`, `fail` or `inconclusive`. A result for a different plan, or one with unplanned or duplicated cells, is `invalid`.

### Changed

- **External-catalog smoke gate** — `scripts/check_smoke_runs.py` now declares its planned cells and checks them with `completeness`. A planned cell missing from `runs.json` fails the gate even when every count in the summary is clean.
