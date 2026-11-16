<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Fixed

- **An arms run is saved when its scores overflow the significance tests.** Scores whose paired difference or interaction contrast leave the float range made `ArmRunner` raise `OverflowError` after the arms had run, so the answers and evaluations were never written. The run is now kept, the refused section is left out, and `summary.significance_error`, `summary.interactions_error` or the arm's `delta_error` says why, and the report prints each refusal (#750). The per-arm averages and deltas of such scores are averaged exactly, or refused when a single difference leaves the range, so the saved file holds no `Infinity`; `arms run` prints a refused delta in its console summary.
