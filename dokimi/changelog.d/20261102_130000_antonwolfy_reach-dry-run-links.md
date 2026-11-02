<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Fixed

- **`repo reach --dry-run` plans the scenarios a run executes** — the dry run and the `--only` check planned from
  every probed path, a link the run cannot follow included, so `sub/AGENTS.md -> nowhere.md` listed `cwd=sub`,
  `read=sub` (and `shadow=sub`) though a run lists that link under `unresolved` and probes nothing in `sub/`. Both
  now plan from the run's own list, so the dry run leaves those scenarios out and `--only read=sub` is a usage
  error there (exit 2). The dry run's `files` still lists every probed path. A tree whose every guidance file is
  such a link (`AGENTS.md -> nowhere.md` alone) made the dry run list `cwd=.` and exit 0 while a run stopped with
  `no guidance file to probe`; the dry run and the `--only` check now stop with the run's message and exit code (1).
