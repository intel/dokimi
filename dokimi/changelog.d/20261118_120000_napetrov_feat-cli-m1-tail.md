<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Changed

- **`explore list` moved to `eval tasks list --format localization` (#94 M1).** `--tasks-root` and `--repo-root` keep their meaning; the default `--format terminal-bench` is the old `eval tasks list`. The old `explore list` spelling exits 2 and prints the new command with the same arguments; nothing runs.
- **`repo validate` moved to `eval check --schema-only` (#94 M1).** The manifest and task.json checks, `--manifest`, `--manifest-file`, `--cache-dir`, `--json` and `--fail-on` keep their meaning; the report is `--json` on stdout (there is no `--out FILE` for it, redirect instead). A self-check option set to a value other than its default (`--task`, `--suite`, `--image`, `--warm`, `--arm`, `--out-dir`, ...) is refused with `--schema-only`, and `--repo-root` is refused under `eval tasks list`'s default format. The old `repo validate` spelling exits 2 and prints the new command. The `--json` document's `kind` is now `eval check` (its `data` is unchanged); scripts that match `kind: "repo validate"` must switch.
