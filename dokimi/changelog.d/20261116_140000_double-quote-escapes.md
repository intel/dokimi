<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Fixed

- **`path-missing` reads a backslash inside double quotes as a shell does** — in `"assets/a\;b.svg"` the backslash is kept (`\;`, `\,`, `\'` and `\ ` are no escapes there), and `"a\\b"` is one backslash; only `\"` and `\\` are swapped. Outside quotes nothing changes.
