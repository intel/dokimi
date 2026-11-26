### Fixed

- **`skill novelty view --atom-labels` refuses what it used to misread** — a label named `all` or `unlabelled` is refused, since the view uses those names for its own buckets; a label `0` is kept (it was dropped as empty); a CSV with a quoted newline or a U+2028 inside a value keeps the row whole; a JSON line that is not an object or exceeds decoder limits names its line; an empty labels file is refused rather than read as no labels; `--atom-labels ""` is refused rather than ignored.

### Removed

- **`skill novelty atoms` and `probe` no longer take `--cache-dir`** — they never read it, and it was silently ignored. A script that passes it now fails with exit 2. `skill novelty analyze` and `skill shorten` keep it.

### Documentation

- **`--cache-dir` help is per command** — `skill novelty analyze` and `skill shorten` take `--cache-dir`, the retention memo. Without the response cache (`DOKIMI_RESPONSE_CACHE`), `analyze` pays again for every model call except memoized retention adjudications. `shorten` pays only for retention adjudications missing from the memo.
