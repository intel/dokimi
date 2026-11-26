<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Fixed

- **`memory lint` blanks the code spans and comments of a line in one pass** — it rebuilt the whole line for every code span and comment, and searched the rest of the line for the next `<!--` after each one, so the time grew with the square of the line length: a line of `` `a` `` repeated to 200000 characters took 5.2 s, 15 times what 50000 characters took. The blanked ranges are now collected and joined once, and the next `<!--` and the missing `-->` are searched for once: 0.12 s at 200000 characters, 4 times what 50000 take. The text blanked, the open comment and the unpaired backtick run it reports are unchanged.
