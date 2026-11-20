<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Added

- **`catalog check --listing-budget CHARS` reports the skill listing size (#970).** The total is each offered
  skill's name plus its description and `when_to_use` capped at 1,536 characters, identical copies counted once. Above `CHARS`
  it warns and names the longest lines until they cover the excess. It is advisory and does not change the
  exit code; `--per-skill` and `--kind tasks` exit 2.
