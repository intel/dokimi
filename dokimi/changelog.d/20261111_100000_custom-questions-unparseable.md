<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Fixed

- **`evaluate --custom-questions` with an empty, non-JSON or non-UTF-8 file exits 2** — it ended in a `JSONDecodeError` or `UnicodeDecodeError` traceback (exit 1) after the output directories were created; it now stops before any of that with `Error: --custom-questions <path>: <reason>` (exit 2). `evaluate` has no `--questions-from`; `EvaluationPipeline(questions_from=<path>)` given a file that is not UTF-8 or JSON raises `InputError` with `--questions-from <path>: <reason>`, and `benchmark run` and `benchmark preflight` keep their own refusal. The file is parsed up front only where the run reads it: a re-run that reuses the cached `questions/<product>.json` (no `--force-regen`) or a `questions_from` run never reads it and is not refused. A golden set found under `--questions-root` and the commands that read `--questions` are unchanged (#576).
