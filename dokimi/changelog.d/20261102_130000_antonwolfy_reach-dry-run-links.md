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
- **`repo reach --dry-run` refuses what a run refuses before its first harness call** — the dry run printed its
  plan and exited 0 where a run stopped before calling the harness: a selection that leaves no scenario (every
  shadow path already in the tree, exit 2), `--format receipt` over a link to `reach.json` (2), a tree with no
  guidance file, a guidance file above the workspace, a guidance file that is not UTF-8, a tree path the workspace
  cannot hold, and a shadow path under a probe file (1). The dry run now makes the same checks, building the
  workspace and removing it, and stops before the harness's version command, with the run's message and exit code.
- **`repo reach` with a `--work-dir` it cannot use fails with a message, not a traceback** — a `--work-dir` that is
  missing, not a directory or not writable raised `FileNotFoundError`, `NotADirectoryError` or `PermissionError`
  from `tempfile.mkdtemp`, and a write the workspace refused while it was seeded (a full disk) raised `OSError`, in
  a dry run and a run alike. Each is now a `✗ cannot make the probe workspace in --work-dir <dir>: <reason>` (or
  `cannot build the probe workspace <dir>: <reason>`) line and exit 1.
