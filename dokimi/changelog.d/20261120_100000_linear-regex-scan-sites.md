<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Fixed

- **Three more long single-line inputs are read in linear time** — a multiple-choice reply with `**a` or `$a` and thousands of spaces and no closer (`chosen_letters`; 6000 spaces took 17 ms, 12 times what 1500 took), a README code span of a capital, many lowercase letters and many capitals that is not a name (`repo scan`; 800 + 800 characters took 1 s, 59 times what 200 + 200 took), and a C header line with thousands of quotes and backslashes and no closing quote (`skill novelty ground --header`; 6000 repeats of `"a\` took 0.52 s, 14 times what 1500 took). The results are unchanged: the same letters, the same identifier spans and the same names read from the header.
