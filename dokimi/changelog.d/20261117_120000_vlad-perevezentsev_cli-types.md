<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Changed

- **A refused integer, USD amount or 0-1 number reads the same on every flag that takes one.**
  `trace prefilter --rare-max`, `eval tasks generate --references` and `--derives`, and
  `eval leak --verbatim-words` say `expected an integer, got
  'x'` or `must be >= N, got V`, like the other integer flags; `tools preference --max-cost-usd`
  says `must be a positive number of USD, got 'x'`, like `--max-usd`. A value that is not a
  number at all no longer prints argparse's `invalid _unit_rate value` (`--in-use-rate`) or
  `invalid usd_amount value` (`--max-usd`) but the flag's own message. The same values are
  accepted and refused as before, with exit 2 (#229).
