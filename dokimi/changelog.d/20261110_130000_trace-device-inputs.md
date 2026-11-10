<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Fixed

- **`trace inspect` and `trace consistency` refuse a device instead of reading it without end** — a trace given as `/dev/zero`, and `trace consistency --outcomes /dev/zero` or `--contract /dev/zero`, were read until memory ran out (a `MemoryError` traceback), and `/dev/null` was read as an empty file. A device among the traces is now reported (`✗ <path>: not a file`), the other traces are still read and the exit code is 1, as for any unreadable trace. A device as `--outcomes` or `--contract` stops with `✗ --outcomes <path>: not a file` (exit 1). A regular file, a pipe and a directory are read as before, and a missing file keeps its message (#533).
