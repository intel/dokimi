<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Added

- **`repo run --candidates FILE`: a `choice` event for an install, import, use or removal of a candidate.** A file
  maps each candidate id to package names (`install`, `remove`), modules an added `import` line names (`import`) and
  dotted names an added line mentions (`use`). A `choice` event follows the `command` or `edit` event that does it, with
  its `candidate`, `act` and, for an edit, its `path`. Only added text counts: an edit's new side, a write's content.
  Copilot's edit tools and Codex's `file_change` carry no added text, so they give install and remove events only. Existing event kinds are
  unchanged.

- `repo run` includes manifest probe patterns in the execution ID. Resuming a batch with different probe inputs requires a new batch or `--force`. Probe matching now respects command exits, short circuits, shell heredocs and wrapper commands.
