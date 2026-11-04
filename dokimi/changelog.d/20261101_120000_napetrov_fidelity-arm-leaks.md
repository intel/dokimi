<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Changed

- **docker-claude arms no longer show dokimi's own names to the agent** — the host workspace is a plain `mktemp` directory instead of `dokimi-run-*`, the in-container skill plugin is mounted at `/opt/claude-plugins` instead of `/opt/abskill`, and the prompt is the task instruction with only the treatment notice in front of it, not the "Solve this coding task…" wrapper. Skills still load through `--plugin-dir`: a skill in `$CLAUDE_CONFIG_DIR/skills` is not offered under the launch's `--setting-sources ""`. The agent binary and its files live under `/opt/agent` instead of `/opt/dokimi-agent`. `tests/test_payload_audit.py` asserts the arms' full command (executable and argv, docker and host) and cwd carry no marker. Runs measured before this change are not comparable in their prompt text or command line.
