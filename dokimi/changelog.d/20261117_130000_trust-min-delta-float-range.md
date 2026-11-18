<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Fixed

- **The trust gate reads a `min_delta` whose arm means differ by more than the float range as not measured.** `report generate --format json` wrote `Infinity` (not standard JSON) as that check's value and marked it passed for scores such as 1.7e308 and -1.7e308. The check now has `value` null, `measured` false and a "Not measured" message, and the verdict lists `min_delta` under `not_measured` (#830).
