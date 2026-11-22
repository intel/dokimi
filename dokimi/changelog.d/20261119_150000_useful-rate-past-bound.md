<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Fixed

- **`repo compare`'s `first_useful_rate` leaves a cell whose `first_useful.steps` is past the 1e150 measure bound out of the rate and its `n`, as every other measure does.** It counted the cell as a non-hit, so steps 5 and 1e200 gave a rate of 0.5 with `n` 2. They now give 1.0 with `n` 1, and the header's "first useful observed" count shows the cell as unmeasured. A cell with no hit (`never`) is still a miss (#922).
