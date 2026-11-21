<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Fixed

- **A model reply that opens many `[` or `{` and never closes them is rejected at once** — `extract_json_array` and `extract_json_object` took their last-resort block with a regex that backtracked quadratically (about 10 s for 100000 openers), and the fence regex did the same on a fence followed by a long run of whitespace. Both now find the block with `str.find` and `str.rfind`, and the fence regex no longer retries per space. The text each function returns or rejects is unchanged.
