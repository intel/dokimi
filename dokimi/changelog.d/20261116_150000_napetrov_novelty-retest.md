<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Added

- **`skill novelty retest`: noise floor from two analyze runs of the same items, no model calls.**
  Run the items again with another `--seed` and a fresh `--cache-dir`, then
  `python cli.py skill novelty retest --baseline DIR --retest DIR --out-dir OUT`. It pairs the runs by
  artifact ref and reports the floor (1.96 × the spread of `second − first` on `k0.lenient`, or
  `--scoring strict`), Pearson r, mean |diff|, mean shift, and which pairs of items differ by less
  than the floor. `retest.md` and versioned `retest.json` are written to `OUT`. Corpus check: 10 skills, r 0.86,
  mean |diff| 0.10, floor about 0.21. See docs/skill-novelty.md, "Test-retest noise of the headline score".
