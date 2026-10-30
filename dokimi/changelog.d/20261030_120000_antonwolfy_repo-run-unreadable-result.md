<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Fixed

- **An unreadable `result.json` no longer ends `repo run`** — a rerun of a
  batch with a cell whose `result.json` was truncated or not UTF-8 stopped the
  whole batch with a bare JSON or decode error, and one holding a JSON list
  refused the batch as another execution (`unrecorded`). That cell is now
  logged as `✗ <cell>: <path>: ...` with how to recover (remove the file, or
  `--force`), the other cells still run or are reused, and the command exits 1
  as for any other crashed cell. The file is left as it is.
