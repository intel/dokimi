<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Changed

- **`report summary` exits 2 on a missing or empty input** (#94, A2). A path given on the command line that does not exist, or inputs holding no `*.json` file at all (an empty directory alone), is refused with `Error: ...` and writes nothing, so a gate reading the summary no longer passes on a typo. An existing directory with no `*.json` beside other inputs still shows a `not_run` row. A document that exists but is unreadable or of another schema is still an `invalid` row.
