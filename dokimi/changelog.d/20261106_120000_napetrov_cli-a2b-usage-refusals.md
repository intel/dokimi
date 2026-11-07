<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Changed

- **Input refusals in `catalog`, `catalog eval`, `catalog probe`, `tasks`, `explore`, `subjects`, `inputs`, `tools`, `novelty` and `baseline regression` print `Error: <message>`** — they printed the message with no prefix, and now go through the same path as every other refusal. The exit code is still 2.
