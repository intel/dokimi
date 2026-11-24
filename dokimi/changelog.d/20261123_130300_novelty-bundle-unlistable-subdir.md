<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Fixed

- **A novelty bundle raises when a subdirectory of the skill cannot be listed** — `load_bundle` listed the skill's files with `Path.rglob`, which skips a subdirectory it cannot list (mode `0o111`) without an error, so the bundle, its `measured_digest` and `resource_words_on_disk` described a smaller skill and the dropped files were not recorded as excluded. It now raises `PermissionError` naming the directory. A skill that can be listed loads as before.
