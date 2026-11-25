<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Changed

- **`report summary` exits 2 on a missing or empty input** (#94, A2). A missing path now exits 2 instead of producing a `not_run` row. Inputs with no `*.json` files also exit 2. The `actions/report` action therefore fails when its output directory contains no `*.json` files, including when upstream steps were skipped. If sticky comments are enabled, the action replaces any previous summary with a render failure message. An empty directory alongside other inputs still produces a `not_run` row. An unreadable document or a document of another schema still produces an `invalid` row.
