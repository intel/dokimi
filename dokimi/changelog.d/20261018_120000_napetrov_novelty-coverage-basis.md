### Changed

- **The novelty gate's `artifact_coverage` judges readable words** — its unmeasured share
  now leaves role decisions (such as a skill's own `evals/`) out of the denominator, the
  same basis as the `novelty analyze` warning and the report, so the gate and the warning
  quote one figure (56% rather than 26% for 400 entry-point words, a 500-word reference
  and 1,000 words of evals; that entry-point run now gets a WARN). Freshly measured
  entry-point runs of skills with their own evals score higher: on 71 public skills, 10
  moved up by +0.040 to +0.134, none crossed the 0.5 ceiling, and no bundle run moved.
  New `bundle` records (`novelty.v1`, `atoms.json`, `probes.json`) carry
  `coverage_basis: "readable"`, and the gate check in the scorecard carries it as `basis`.
  A record without the stamp, which is every artifact stored before this release, is still
  gated on all bundled words, so re-gating it gives the value it had; a stamp this version
  does not know is reported as not measured. Do not compare `artifact_coverage` values
  across the two bases.
