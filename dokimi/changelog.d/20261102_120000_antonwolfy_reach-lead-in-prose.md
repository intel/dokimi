<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Fixed

- **A lead-in line no longer trips the reach prose warning** — `repo reach` no longer counts, in a scenario's `prose`, the answer's first non-empty line when it has at most 12 words, ends in `:` and a later line holds a token (`Now I'll list all verification tokens found in my context:` followed by the tokens counted 10 words and printed "answered with 10 words besides tokens" although every token was reported). Any other words still count: a sentence after the tokens, a lead-in with no token after it, a first line not ending in `:`, a first line of more than 12 words (an explanation or a refusal ending in `:`).
