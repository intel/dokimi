<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Added

- **`tasks run --harnesses docker-null`: the null check, a task's untouched start must fail its tests.**
  The cell builds the task image, runs no solve and runs the verifier. A task passes when its
  `docker-oracle` cell passes and its `docker-null` cell is `null_fail` (the tests ran and some
  failed). A pytest setup or collection error is `fixture_error`, not a pass. `docker-null` needs
  `docker-oracle` in the same run; the run exits 1 when any task does not pass both (intel/dokimi#808).

### Fixed

- **`tasks run --help` names `docker-oracle` and `docker-null`** in the `--harnesses` list.
