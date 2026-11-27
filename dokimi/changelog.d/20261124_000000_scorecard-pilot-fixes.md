<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Fixed

- Repository scans flag missing configuration paths and name missing link targets. `--fail-on-fail` works with a deprecation warning for one release.
- Catalog migration hints include the required arguments, and repository task-check hints show how to validate schemas without Docker.
- Need graph extraction accepts literal control characters inside JSON strings. `eval graph --json` prints the published graph.
- Task harness output always uses a `run<N>` directory, including single-repeat runs.
