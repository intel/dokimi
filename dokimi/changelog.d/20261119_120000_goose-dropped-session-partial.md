### Fixed

- **`trace prefilter --json` is now `partial` when a Goose session left out of the report had a skipped message** — a session whose only messages were unreadable (content that is not JSON or not a list of blocks) was dropped from the corpus and named on stderr, but `status` stayed `complete`. Its skipped records now make the status `partial`, as `docs/session-prefilter.md` says. (#938)
