### Fixed

- **`repo security` reads a line of repeated `curl`, `wget` or `pip install` in linear time.** The `pipe-to-shell` and `unpinned-install` rules read the rest of the segment again from each opener, so a line of 3000 `curl x ` took quadratic time. They now read each segment from its first opener, and find the same lines (#1269).
