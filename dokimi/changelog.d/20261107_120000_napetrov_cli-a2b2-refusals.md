<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Changed

- **Input refusals in `eval personas validate` and `eval personas discover`, `subjects`, `dashboard generate`, the `--python-env` and `--typecheck-tool` refusals of `repo scan`, and `repo skill-reach` print `Error: <message>`** — the ones that printed a `✗`, `⚠` or `Error loading ...` line now print the same `Error:` line as every other refusal. The exit code is still 2.
