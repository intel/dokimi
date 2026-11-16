<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Fixed

- **A NaN judge score no longer gives its measure a direction in `eval judge cross`.** A measure whose mean delta was NaN was read as a regression, so one NaN score could turn an arm's label into `LEANING −`, `JUDGE-DEPENDENT`, or, when the measure was alone and significant, `CONSISTENT −`. Such a measure now has no direction, whether or not its p-value is significant, so the arm reads `NO DETECTABLE EFFECT` or follows the other measures. NaN pairs still count as ties in wins and losses. Outputs for finite scores are unchanged.
