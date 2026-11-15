<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Fixed

- **An empty answer list no longer judges a previous run's answers file** — the evaluate step fell back to the answers file on disk whenever the in-memory list was empty, and judged it as legacy method version 1. The fallback now applies only when no answers were passed at all.
- **A run with an empty question set fails instead of ending as a success** — a cached, `--questions-from` or generated set with no questions is refused with `No questions to answer`, before any answer call, as the empty golden set already was. In `benchmark run`, an empty cached or `--questions-from` set is an input refusal (exit 2, no traceback), made before persona discovery; `evaluate` fails with exit 1.
- **`benchmark run --questions-from` refuses an empty question file as an input** — `{"questions": []}` is refused with `questions source is empty` before the run starts, instead of ending in a traceback; `benchmark preflight` refuses it too, where it reported `Preflight OK`.
