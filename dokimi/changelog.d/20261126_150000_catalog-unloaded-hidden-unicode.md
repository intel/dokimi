<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Fixed

- **`catalog check` scans the bundled files of a skill that fails to load for `hidden-unicode` and `legacy-model-id` (#1089).**
  A skill whose front matter is broken YAML, or a nested one, got no content check, so a hidden character in its
  `notes.md` went unreported. Its row now carries those findings next to `frontmatter-parse`.
