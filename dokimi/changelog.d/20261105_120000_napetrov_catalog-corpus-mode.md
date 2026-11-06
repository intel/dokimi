<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Added

- **`catalog scan` corpus mode for large catalogs** — `--per-skill` reviews each skill alone, so the
  cross-catalog checks (`duplicate`, `name-conflict`, `mirror-drift`, `description-near-duplicate`) do
  not fire between unrelated skills. A `SKILL.md` nested in another skill's directory is marked with
  `nested_in` in JSON and with a `note` in text; `--skip-nested` leaves it out. With `--per-skill`,
  `--jsonl PATH` streams one row per skill, `--jobs N` reviews in parallel, and `--resume` skips the
  skills already in PATH. `--jsonl`, `--jobs` and `--resume` need `--per-skill` (`--resume` needs
  `--jsonl`); other misuse exits 2. From Python, `dokimi.external.review_skill_dir(path)` returns the
  `SkillReview` of one skill directory.
