<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Changed

- **The changed-lines mutation check reports `inconclusive` for a survivor whose module has a candidate
  test file that did not run.** That file, left for lack of time or failing to collect, may be the one
  that catches the change, so the survivor is not counted as a gap. It is still listed with a warning
  and does not change the exit code. Developer tooling only (`scripts/mutate_changed.py`).
