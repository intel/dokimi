<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Changed

- **Input refusals in `catalog`, `catalog eval`, `catalog probe`, `tasks`, `explore`, `subjects`, `inputs`, `tools`, `novelty` and `baseline regression` print `Error: <message>`** — the ones that printed the bare message now go through the same path as every other refusal; the rest already printed the prefix. The exit code is still 2.
