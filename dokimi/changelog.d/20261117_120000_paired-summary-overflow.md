<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Fixed

- **The evaluate pipeline and the reports finish when a paired score difference leaves the float range.** `paired_summary` raised `OverflowError` for scores such as 1.7e308 and -1.7e308 in one pair, which ended `dokimi orchestrate` after the evaluations were saved and failed `report generate` and `report eval`. It now returns the arm means, ranges and counts with no delta (`delta_mean`, `delta_min` and `delta_max` are `None`, `delta_overflow` counts the pairs); improvements and degradations of such a pair are counted by comparing its two scores. The pipeline prints `delta=none`, the reports print an em dash with a one-line note, and `report eval` names the refused significance test instead of stopping (#830).
