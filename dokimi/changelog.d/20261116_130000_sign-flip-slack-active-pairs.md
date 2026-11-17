### Fixed

- **An unchanged pair of large values no longer widens the sign-flip slack on the sums of small changed pairs** — `paired_test([1e-12] * 8 + [1.0], [0.0] * 8 + [1.0])` gave `p` 1.0 and no significance, where the eight small pairs alone give 0.0078125, because the slack on a pattern's sum followed the largest value of any pair, tied ones included. The slack now follows the pairs that are summed (the changed ones, and at a shift of the interval search the deltas that are not ties there), in the test and in the interval that inverts it.
