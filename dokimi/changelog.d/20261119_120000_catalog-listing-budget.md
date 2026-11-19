<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Added

- **`catalog check --listing-budget CHARS` (#966).** The total of the skill listing (each listed skill's
  `name: description`, plus ` - when_to_use` when set) is in the report under `listing`. Over `CHARS` is a
  `listing-over-budget` warning, and exit 1 with `--strict`; `--ignore listing-over-budget` drops both. The
  whole-catalog scan, `--per-skill`, `--jobs` and `--resume` count alike. Each `--jsonl` row has a new
  `listing_chars`, so `--resume` reviews again a receipt written without it.
