<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Fixed

- **OpenCode skills no longer read as errors** — a `.opencode/skills/` `SKILL.md` follows OpenCode's rules (frontmatter optional, name not checked against the directory, no description cap) instead of the Agent Skills spec. A file with no frontmatter gets one `warn`, `opencode-no-description`, and a name that is not kebab-case is a `warn`. On a sample of 200 `.opencode` files, the share with an error fell from 81.5% to 1.5%. A `SKILL.md` under a listed host directory with no known spec (`.cursor`, `.kiro`, ...) gets one `note`, `unsupported-skill-format`, instead of spec errors, and an unloaded one is `skipped`, not `ok` (`catalog check --json` counts it in `summary.skills.skipped`). OpenCode's global `~/.config/opencode/skills` is read as OpenCode, and `metadata: {opencode/autoinvoke: false}` hides a skill as `disable-model-invocation` does. Other hidden directories, such as `~/.config` itself, keep the spec checks. An unloaded `SKILL.md` under either rule still gets the command checks and `allowed-tools-unrestricted-bash`, so `--strict` fails a Bash grant with a pipe to a shell there, as on a loaded skill. An OpenCode skill whose only errors were its name, and a skill under a host with no known spec, now join the routing field: `catalog routing`, `skill routing`, `skill novelty` and the granularity hints of `catalog check` count them, so results on catalogs that hold such skills are not comparable with earlier runs.
