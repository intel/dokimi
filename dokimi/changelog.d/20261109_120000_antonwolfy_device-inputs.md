<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Fixed

- **Input-file flags refuse a device instead of reading it without end** — `eval personas validate --file`,
  `evaluate --custom-questions`, `benchmark preflight|run --questions-from`, `eval questions generate --personas`
  and `--topics`, `--placebo-text` (`skill novelty analyze|probe`), every flag read through `read_json_input`
  (`eval triggers generate --trigger-set` among them) and the guidance file of `trace prefilter` read a device
  such as `/dev/zero` until memory ran out (a `MemoryError` traceback, or for `benchmark` the empty
  "questions source invalid: /dev/zero ()"), and read `/dev/null` as empty input. They now exit 2 with
  `Error: <flag> <path>: not a file` before reading (`guidance <path>` for `trace prefilter`). A regular file, a
  pipe and a directory are read as before, and a missing file keeps its message. A device among the sessions of
  `trace prefilter` is reported (`✗ <path>: not a file`) and the rest are still read; the exit code is 1, as for
  any unreadable session (#504).
