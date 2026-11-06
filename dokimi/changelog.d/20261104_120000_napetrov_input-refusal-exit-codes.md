<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Changed

- **Input refusals print `Error: <message>` on stderr and exit 2 through one path** — more
  refusals in `eval score`, `eval judge`, `eval headroom`, `eval redteam`, `eval import`,
  `answers generate`, `data export`, `data import docling`, `run` and `baseline compare` now
  raise `InputError`, which `cli.main` turns into the same
  `Error: ...` line and exit 2, so a message that used to be printed and exited with
  `sys.exit(2)` reads the same and no traceback escapes. A cross-judge run with several
  stored-material problems prints one `Error:` line, with the problems below it.
  `baseline compare` on an eval record it cannot read now prints `Error: <message>` where it
  printed `❌ <message>`; the exit stays 2.

- **Run failures exit 1, not 2** — `eval score pairwise` in which every comparison failed
  (the errors are still written to `--output`), and a `--judges` judge that scored none of its
  answers in `eval judge agreement --judges`, now exit 1 like other run failures
  after writing their report. Scripts that treated 2 as "bad input" for these runs must check 1.
