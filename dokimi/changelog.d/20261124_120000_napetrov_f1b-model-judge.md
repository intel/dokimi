<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Changed

- **Pass the model and its provider together as `--model PROVIDER/MODEL`.**
  `--provider X --model M` becomes `--model X/M`. On commands that migrated their judge
  flags, `--judge-provider X --judge-model M` becomes `--judge X/M`. `arms run`,
  `arms matrix-run`, and `subjects run` keep the separate flags and the `--judge` switch.
  Novelty commands keep the separate flags as an alternative to `--judge PROVIDER/MODEL`
  panel entries. `--extract-model` takes `PROVIDER/MODEL` too (`--extract-provider` is gone),
  and `eval judge agreement --judges a,b` becomes `--judge a --judge b`. A bare model still
  uses the command's default provider. Moved flag spellings print migration guidance and exit 2.
  Part of #94 (F1b).
