<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Fixed

- **`trace prefilter` reads a command with a long run of `$((` and no `))` in linear time** — the heredoc scan blanked arithmetic with a lazy regex that rescanned to the end of the line from every opener (3 s for 20000 openers). It now finds each closer with `str.find` and stops at the first opener that has none. The text it blanks is unchanged.
