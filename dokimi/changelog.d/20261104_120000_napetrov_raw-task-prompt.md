<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Changed

- **Command arms (`command:` and `--command-template`) send the task instruction as written** — the `Solve this coding task. Work only inside the task environment…` and `# Task: <name>` lines that dokimi put around `{prompt}` are gone, as a user's agent never sees them. `{prompt}` and `{prompt_quoted}` now expand to the bare `instruction.md`, and `{prompt_escaped}` to the same text with `{` and `}` doubled. Command-arm runs measured before this change are not comparable in their prompt text.
