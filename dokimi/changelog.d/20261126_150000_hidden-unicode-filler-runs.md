<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Fixed

- **`catalog check` reports a run of more than three blank fillers on one line as `hidden-unicode`** — U+034F beside a non-ASCII letter and U+115F or U+1160 beside Hangul stay exempt when a line holds up to three, but a fourth makes every one of them on that line a finding, since a long run carries a bit pattern a reader cannot see. A line of polytonic Greek with more than three U+034F is now reported. U+2800 in Braille is not counted.
