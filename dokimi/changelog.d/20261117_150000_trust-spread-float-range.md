<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Fixed

- **The trust gate reads a `score_cv` or `multirun_variance` whose spread leaves the float range as not measured.** `evaluate_trust`, and with it `ReportGenerator.generate_report`, raised `OverflowError: integer division result too large for a float` for context-arm scores such as 1.7e308, -1.7e308 and 1.7e308, or per-run averages like them. Each check now has `value` null, `measured` false and a "Not measured" message, and the verdict lists it under `not_measured` (#870).
