<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Changed

- **A `docker-*` verifier must write `reward.txt`: exit 0 alone is no longer a pass** — a Docker task whose verifier wrote no `/logs/verifier/reward.txt` used to score 1.0 on exit code 0. The cell is now errored, with no reward, and `run.log` names the missing file. A verifier that exits non-zero without a reward file still scores 0.0. A `reward.txt` with no number in it (`PASS`, empty) now errors the cell too, instead of scoring a fail, and so does a `docker cp` of it that fails or times out (the timeout used to score 0.0). Make such a verifier write `1` or `0` to `/logs/verifier/reward.txt`; pass rates of tasks whose verifier only set its exit code are not comparable with earlier runs.
