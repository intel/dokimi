<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Added

- **`events.jsonl` carries a format version, and the format is documented.** Every line of a
  repository run's `events.jsonl` now has `v` (1); a line without it is read as version 1, so
  older traces keep working. `docs/events-format.md` describes the lines and the promise that
  within a major version kinds and fields are only added, and readers skip a kind they do not
  know. The schema is `schemas/repo_event.v1.json`.
