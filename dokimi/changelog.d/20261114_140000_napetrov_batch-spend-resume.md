<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Fixed

- **`repo run`: the batch cap (`--max-batch-usd`) counts what earlier invocations of the batch spent.** The
  total is kept in `_eval/spend.json` under the batch lock, so a resumed run continues the cap instead of
  starting it at zero. A cell's charge is saved even when its `result.json` cannot be written.
- **`repo run --jobs N`: the cap is not passed at once.** Each cell in flight reserves its harness budget
  (the task's `budget_usd`, else `--budget`) against the cap until its charge is known, so cells started
  together cannot start past the cap. A cell can still spend up to its own harness cap.
- A batch made before this change has no `_eval/spend.json`: its cap starts at zero on resume, as before.
  A `spend.json` that cannot be written is reported as a run error; the run's own count stays right.
- With `--max-batch-usd`, a `_eval/spend.json` that cannot be read, or holds no usable total, stops the run
  with its path; remove it to start the cap again. Without a cap the file is not enforced: the run counts
  it as 0 and replaces it with its own total.
