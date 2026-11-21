<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Added

- **`catalog check --listing-budget CHARS` (#966).** The total of the skill listing (each listed skill's
  `name: description`, plus ` - when_to_use` when set) is in the report under `listing`. Over `CHARS` is a
  `listing-over-budget` warning, and exit 1 with `--strict`; `--ignore listing-over-budget` drops both. The
  whole-catalog scan, `--per-skill`, `--concurrency` and `--resume` count alike. Each `--jsonl` row has two new
  fields, `listing_chars` and `listing_name` (the loaded name the total groups copies by, not the manifest name), so
  `--resume` reviews again a receipt written without them and refuses a `listing_chars` that is not a nonnegative integer.
