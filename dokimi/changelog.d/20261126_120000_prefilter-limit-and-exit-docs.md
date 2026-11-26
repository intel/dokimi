<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Fixed

- **`trace prefilter`: a limit written in words is `kept_protected`, and the page states the exit codes.** A rule such as `Keep functions under 50 lines.`, `Use at most 3 retries.` or `Limit a pull request to 400 changed lines.` now carries the `threshold` protection instead of being ranked as a candidate for ablation. `docs/session-prefilter.md` now says that no readable session gives exit 2 and a `failed` document, and that a failed subagent transcript is a warning with exit 0 (#937, #1312).
