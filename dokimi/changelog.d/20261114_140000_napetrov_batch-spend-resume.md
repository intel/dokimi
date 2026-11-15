<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Fixed

- **`repo run`: the batch cap (`--max-batch-usd`) counts what earlier invocations of the batch spent.** The
  total is kept in `_eval/spend.json` under the batch lock, so a resumed run continues the cap instead of
  starting it at zero. A cell's charge is saved even when its `result.json` cannot be written.
- **`repo run --jobs N`: the cap is not passed at once.** Each cell in flight reserves its harness budget
  (`--budget`) against the cap until its charge is known, so cells started together cannot overshoot it.
  The reservation is the cell's ceiling, so the cap can stop a batch earlier than the spend would.
- A `_eval/spend.json` that cannot be read stops the run with its path; remove it to start the cap again.
