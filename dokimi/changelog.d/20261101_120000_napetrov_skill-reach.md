<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Added

- **`dokimi repo skill-reach`: which skills a harness actually lists** — places token-seeded `SKILL.md` fixtures from a catalogue (`repos/skill_fixtures.yaml`, or `--catalogue`) in a fresh workspace, HOME and config dir, asks the agent to report the skills it can see, and classifies each as `listed`, `fetched`, `not_listed` or `error`, with a verdict (`one`, `all`, `some`, `none`) per same-name collision group. Covers project and user client dirs, `.agents/skills`, subdirectories, nesting, symlinks and description formats. Harness profiles gain a `skills` block (`project_dirs`, `user_dirs`) and a `skills` tool mode; claude-code and codex declare both. Writes `skill-discovery.json` (`skill_discovery.v1`).
