### Added

- **`arms run --judge-context baseline`, and `arms matrix-run`** — the judge is shown no treatment material on any arm: each arm is judged on the context a baseline arm's judge sees, so the judged arms share one context. Use it as the control when measuring how much the material itself moves the judge; the answering path is unchanged. The run is stamped `judge_context: treatment_material.withheld.v1`, which pools only with other withheld runs. The default, `--judge-context arm`, is unchanged.
