<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Changed

- **`novelty routing` split in two (#94).** One skill is now `dokimi skill routing SKILL [--against ROOT]`, where `--against` replaces `--catalog`. All skills against each other is `dokimi catalog routing ROOT --reps N`, where `--reps` replaces `--repeats` and `--all-vs-all` is gone. The old `novelty routing` spelling exits 2 and prints the new command, with the same arguments rewritten; nothing runs. A bare `--artifact` or `--competitors` path was text, so the printed command says `text:` for it: `skill routing` reads a bare path as a skill. `--all-vs-all` with `--competitors` and no `--catalog` has no replacement, and exits 2. `--reps` replaces `--repeats` in both commands, not only in `catalog routing`. Scripts that call `novelty routing` must switch to the new spelling.
