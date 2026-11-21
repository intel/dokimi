### Fixed

- **`trace prefilter` now says how many skipped records it did not list** — a session names at most 20 skipped records on stderr, and the 21st and later were dropped without a word, so a Goose store with 21 malformed messages printed what one with 20 printed. Each such session now gets a final `warning: <session>: N more skipped records not listed`. The status stays `partial`. (#963)
