<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Changed

- **`catalog check --json` names the script a `script-crlf` or `script-syntax` finding is about (#961).** The finding
  gains an optional `script` key: the script's path relative to the skill directory, so two scripts that share a
  basename (`scripts/alpha/run.sh`, `scripts/beta/run.sh`) can be told apart without parsing `message`. `file` still
  names the skill's `SKILL.md`. Other findings do not carry the key, and `cli_output.v1` lists it as optional.
  A client that validates finding keys against a closed set has to accept `script`.
