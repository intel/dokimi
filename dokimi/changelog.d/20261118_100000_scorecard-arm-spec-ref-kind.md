<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Fixed

- **A `baseline_digest` now changes when a file a spec names is replaced by a missing path** — the digest of a spec part took only the part's `ref` digest, so a file whose content spelled what a missing ref hashes to left the arm-spec digest unchanged when the file was deleted. Each part now also carries whether its ref named a file, a directory or neither. `ref_digest` itself is still the sha256 of a single file's content, so a file whose content is a missing ref's hash input still shares that `ref_digest`. Arm-spec digests differ from earlier ones for the same inputs; rebuild a scorecard to get the new value.
