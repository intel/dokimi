<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Changed

- **Spend and dry-run flags have one spelling each (part of #94, F1b).** A cap on the whole command is
  `--max-usd` (`tools preference` `--max-cost-usd`, `repo run` `--max-batch-usd`). A cap per agent run is
  `--max-usd-per-cell` (`repo run`, `repo reach`, `repo skill-reach` `--budget`). An estimate that makes no
  paid call is `--dry-run` (`catalog routing`, `skill routing`, `skill novelty analyze` `--estimate-only`).
  The old spellings exit 2 and print the new command. The values mean what they meant; the JSON output
  keys are unchanged.
