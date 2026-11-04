<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Changed

- **Command and Harbor-style harnesses send the task instruction as written** — the `Solve this coding task. Work only inside the task environment…` and `# Task: <name>` lines that dokimi put around `{prompt}` are gone, as a user's agent never sees them. `{prompt}`, `{prompt_quoted}` and `{prompt_escaped}` now expand to the bare `instruction.md`. Command-arm runs measured before this change are not comparable in their prompt text.
