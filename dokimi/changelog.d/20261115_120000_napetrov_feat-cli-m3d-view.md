<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Changed

- **`novelty view` moved to `skill novelty view` (#94 M3).** It sits next to `skill novelty analyze|atoms|probe|items`, and its flags are unchanged. The old `novelty view` spelling exits 2 and prints the new command with the same arguments; nothing runs. A bare `novelty` prints `skill novelty`. The proposal maps `novelty view` to `report $RUN`, but that name is the report stage's (L5) and is still taken by the old `report` leaves, so it waits for that stage. Scripts that call `novelty view` must switch to `skill novelty view`.
