<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Fixed

- **An unreadable `result.json` no longer ends `repo run`** — a rerun of a
  batch with a cell whose `result.json` was truncated or not UTF-8 stopped the
  whole batch with a bare JSON or decode error, and one holding a JSON list
  refused the batch as another execution (`unrecorded`). In a cell of a task
  this host cannot run, a truncated or non-UTF-8 one was instead counted as
  already done, even with `--force`. That cell is now logged as `✗ <cell>: <path>: ...` with
  how to recover (remove the file, or `--force` where this host can run the
  task), the other cells still run or are reused, and the command exits 1 as
  for any other crashed cell. The file is left as it is.
