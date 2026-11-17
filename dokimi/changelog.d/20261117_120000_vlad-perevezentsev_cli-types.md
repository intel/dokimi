<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Changed

- **A refused number on a few flags reads like the other flags of its kind.**
  `trace prefilter --rare-max`, `eval tasks generate --references` and `--derives`, and
  `eval leak --verbatim-words` say `expected an integer, got 'x'` or `must be >= N, got V`;
  `tools preference --max-cost-usd` says `must be a positive number of USD, got 'x'`. A value
  that is not a number at all no longer prints argparse's `invalid <function> value` on
  `trace prefilter --in-use-rate` and on the `--max-usd` of `skill novelty`, `skill shorten`,
  `skill routing`, `catalog routing`, `eval graph` and `eval skill-coverage`, but the flag's own
  message. These flags accept and refuse the same values as before, with exit 2 (#229).
