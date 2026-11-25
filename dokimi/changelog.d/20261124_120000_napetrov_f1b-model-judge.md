<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Changed

- **The model and its provider are one flag: `--model PROVIDER/MODEL`, `--judge PROVIDER/MODEL` (repeatable).**
  `--provider` is gone (`--provider X --model M` becomes `--model X/M`). Where a judge was
  `--judge-model` with `--judge-provider`, it is `--judge X/M`. `--extract-model` takes
  `PROVIDER/MODEL` too (`--extract-provider` is gone), and `eval judge agreement --judges a,b`
  is `--judge a --judge b`. A bare model still runs on the command's default provider. The old
  spellings print the new argv and exit 2, as the other moved flags do. Part of #94 (F1b).
