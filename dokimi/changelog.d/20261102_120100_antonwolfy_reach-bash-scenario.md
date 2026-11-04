<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Added

- **A `bash=<dir>` reach scenario** — `repo reach --scenarios bash` runs, for each guidance directory, a scenario started at the root with the profile's new `bash` tool mode (`claude-code`: `--tools Bash`) that tells the agent to `cat <dir>/REACH_PROBE.txt` (the path shell-quoted, so a space or `;` in a directory name stays one operand) and then list tokens: it shows whether a shell read of a nested file loads that directory's guidance, as `read=<dir>` shows for the Read tool. The default stays `root,cwd,read`, so a probe costs no more unless asked; `--dry-run` lists the bash scenarios and `--only bash=<dir>` picks one. A profile without a `bash` tool mode (`codex`, `copilot`) asked for `--scenarios bash` is a usage error (exit 2) naming the profile. `harness_profile.v1` accepts an optional `bash` key under `tools` (not under `delegation.none`, so `repo run --tools bash --delegation none` is refused); the receipt treats a `bash=<dir>` run as it does `read=<dir>`. The scenario runs a shell on the host, with the harness's credentials and network, so `claude-code`'s `bash` mode adds a PreToolUse hook that blocks every Bash call but the exact `cat` that `repo reach` names in `DOKIMI_REACH_COMMAND`, and blocks every call when the hook itself fails: a `CLAUDE.md` in the probed repository cannot make the probe run anything else.
