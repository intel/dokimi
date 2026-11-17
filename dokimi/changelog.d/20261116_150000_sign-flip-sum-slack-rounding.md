### Fixed

- **The sign-flip slack on a sum is rounding error, not a tie width** — a ninth changed pair just past its tie width (`1.0 + 1.1e-9` against `1.0`) beside eight pairs of `1e-12` gave `p` 1.0, because its width of `1e-9` let every sign pattern count; it now gives `2/2**9`. The interval starts from an accepted shift when the mean of the deltas is rejected (it could return two rejected ends), and the `mde` of `repo compare` reads the spread at the scale of the changed pairs, so an unchanged pair of large values no longer removes it.
