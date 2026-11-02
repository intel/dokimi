<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Added

- **`subject.distribution_digest` in the subject scorecard** — the `sha256:` digest a `.well-known/agent-skills/index.json` entry (agentskills PR #254) would carry for the subject: the bytes of `SKILL.md` when its directory holds nothing else (a directory ref or a `SKILL.md` file ref alike). A skill with resources is served as an archive, whose bytes a checkout cannot predict, so it gets `null` under either ref, and so does a ref that is not a local path. A client can match a published result to the skill it installed by this field. `ref_digest` is unchanged: a content digest of the tree, which no index carries.
