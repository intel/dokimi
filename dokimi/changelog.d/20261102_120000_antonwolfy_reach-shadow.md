<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Added

- **`repo reach --scenarios shadow` measures what a `CLAUDE.local.md` turns off** — the probe could only show the
  tree as it is, so on a tree with `AGENTS.md` files and no `CLAUDE*` file it could not show that a `CLAUDE*` file
  turns them off. The new `shadow` kind writes the profile's new `guidance.shadow_file` (`CLAUDE.local.md` in the
  built-in `claude-code` profile, and in no other) for one scenario at a time: `shadow=.` at the root, started there
  with tools off, and `shadow=<dir>` in each guidance directory holding a file not named `CLAUDE*`, started at the
  root and told to read a file in `<dir>`. The file carries its own head and tail tokens, is not committed, and is
  removed when its scenario ends, after an error too; a directory where the tree already has a file or a directory
  (an empty one included) at that path, or a file above it, gets no shadow scenario, and a shadow path that would
  replace or go under one of the probe's own files stops the run with an error before any scenario runs. When a
  shadow scenario is planned, the shadow file's name joins the names the probe refuses to find above the workspace:
  with a profile's `shadow_file: MEMORY.md`, a `MEMORY.md` there stops a run and a dry run alike. Its result is a `shadow` record on the scenario row of `reach.json`
  (`guidance_reach.v1`, widened in place) and one line under the matrix, so the matrix keeps one row per tree file.
  `--format receipt` leaves the shadow scenarios out: the receipt describes the tree as committed. `shadow` is not in the default (`root,cwd,read`, which the `--scenarios` help now names instead of "all"), and a
  profile without `guidance.shadow_file` refuses it as a usage error (exit 2). `--dry-run` lists the shadow
  scenarios with the file each writes.
