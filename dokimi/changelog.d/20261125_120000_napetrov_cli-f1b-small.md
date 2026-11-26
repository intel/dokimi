<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Changed

- **Run name, force and correction flags have one spelling each (part of #94, F1b).** A run's name is `--name`:
  `answers generate`, `eval score pointwise` and `report eval` `--run-id`, and `repo run` `--batch`, are now `--name`.
  Regenerating over existing output is `--force`: `benchmark run` and `benchmark batch` `--force-regen` is now
  `--force`. The multiple-comparison correction is `--correction holm|bh`: `arms run`, `arms matrix-run` and
  `report model-compare` `--stats-correction` is now `--correction`. The old spellings exit 2 and print the new
  command. The values mean what they meant; the JSON output keys are unchanged.
