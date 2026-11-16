### Fixed

- **Mean of float deltas no longer depends on the Python version** — `checked_mean`, which gives the `repo compare` summary mean (`sign_flip_summary`) and the per-question means of the model comparison, added floats with `sum()`, which compensates only from Python 3.12: `[1e307] * 6 + [-1e307] * 6` gave `-2.079e290` on 3.11 and `0.0` on 3.12. It now sums with `math.fsum` (exactly rounded), so the mean is the same on every version and agrees with the interval. Integer-only inputs are still summed as exact integers. A mean that is not affected by rounding is unchanged.
