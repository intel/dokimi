<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Fixed

- **A verifier that exits 0 without writing `reward.txt` is an error, not a pass** — a Docker task whose verifier wrote no `/logs/verifier/reward.txt` used to score 1.0 on exit code 0. The cell is now errored, with no reward, and `run.log` names the missing file. A verifier that exits non-zero without a reward file still scores 0.0.
