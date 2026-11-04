<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Fixed

- **`catalog scan` no longer flags a template's placeholder link** — a link whose target is a placeholder, such as
  the `[Title](URL)` of a "you might also find this helpful" line in a skill's reply template, warned `link-missing`
  (`link 'URL' names no file`) because the target names no file. A target that is an all-caps word with no dot or
  slash (`URL`, `LINK`, `PATH`), or an ellipsis (`...`, `<...>`), is now not checked, in an inline link or a
  reference definition. A lowercase, mixed-case, dotted or slashed target (`url`, `some/URL`, `URL.md`) is still
  checked, and links inside code spans and fences were already not checked.
