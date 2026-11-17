### Fixed

- **The sign-flip interval starts from a shift the test accepts** — eight pairs of `1e-12` beside one tied pair of `1.0` gave an interval of `8.9e-13` at both ends, a shift the test rejects, where the eight pairs alone give `1e-12`. A tied delta pulled the mean off the one accepted shift, so the search returned its starting point unchecked; it now starts from the mean of the changed deltas when the mean of all is rejected.
