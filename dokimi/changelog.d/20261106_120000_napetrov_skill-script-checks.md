<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Added

- **`catalog check` checks the scripts a skill bundles** — each `.sh`, `.bash` and `.py` file, and each
  file starting with `#!`, under the skill directory is read, never run or imported. A CRLF line ending
  in a shell or shebang script is an `error` (`script-crlf`); a Python file that `ast.parse` rejects, or a
  shell script that `bash -n` rejects, is an `error` (`script-syntax`). When `ruff` or `shellcheck` is on
  PATH, one `note` (`script-lint`) gives the count and the top rules of each. Missing tools add nothing.
