### Fixed

- **`skill novelty view --atom-labels` refuses what it used to misread** — a label named `all` or `unlabelled` is refused, since the view uses those names for its own buckets; a label `0` is kept (it was dropped as empty); a CSV with a quoted newline or a U+2028 inside a value keeps the row whole; a JSON line that is not an object names its line; an empty labels file is refused rather than read as no labels; `--atom-labels ""` is refused rather than ignored.

### Documentation

- **`--cache-dir` help is per command** — `skill novelty analyze` and `skill shorten` take `--cache-dir`, the retention memo; `skill novelty atoms` and `probe` no longer accept it (they never read it). The help says a rerun without the response cache (`DOKIMI_RESPONSE_CACHE`) pays again for the answer and judge calls, rather than for every call.
