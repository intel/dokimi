<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Fixed

- **An empty answer list no longer judges a previous run's answers file** — the evaluate step fell back to the answers file on disk whenever the in-memory list was empty, and judged it as legacy method version 1. The fallback now applies only when no answers were passed at all.
- **A run with an empty question set fails instead of ending as a success** — a cached, `--questions-from` or generated set with no questions is refused with `No questions to answer`, before any answer call, as the empty golden set already was.
