<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Added

- **Token-free command checks in `catalog check`** — the skill's `SKILL.md` and its bundled text files are
  scanned, with regular expressions only. `pipe-to-shell` warns on `curl` or `wget` piped into a shell or an
  interpreter; `permission-bypass-flag` warns on `--dangerously-skip-permissions`, `--yolo` and
  `bypassPermissions`; `install-unpinned` warns once per skill with the count of `pip`, `npm` and `npx`
  installs that name no version. Installs are read from code (fenced blocks and code spans of prose files, the whole of
  scripts), so prose is not counted; requirements files, local paths, URLs and `git+` sources are skipped. All three
  are warnings and do not change the exit code.
