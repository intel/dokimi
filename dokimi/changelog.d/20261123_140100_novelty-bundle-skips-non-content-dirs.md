<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Fixed

- **A novelty bundle does not list a directory that is not skill content** — `load_bundle` lists a skill's files strictly, and skips a dot-named directory or one named in `SKIP_DIR_NAMES` (`.git`, `venv`, ...) before it is listed, so one of those that cannot be listed does not stop the skill from loading.
