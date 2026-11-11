<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Changed

- **`novelty routing` split in two (#94).** One skill is now `dokimi skill routing SKILL [--against ROOT]`, where `--against` replaces `--catalog`. All skills against each other is `dokimi catalog routing ROOT --reps N`, where `--reps` replaces `--repeats` and `--all-vs-all` is gone. The old `novelty routing` spelling exits 2 and prints the new command, with the same arguments rewritten; nothing runs. Scripts that call `novelty routing` must switch to the new spelling.
