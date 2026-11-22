### Fixed

- **`trace prefilter --json` is now `partial` when a Goose session left out of the report had a skipped message** — a session whose only messages were unreadable (content that is not JSON or not a list of blocks) was dropped from the corpus and named on stderr, but `status` stayed `complete`. Its skipped records now make the status `partial`, as `docs/session-prefilter.md` says. (#938)
- **`trace prefilter --json` is also `partial` when a resumed Claude Code log merged into the one it continues, or a subagent transcript named without its session log, had a skipped line** — the merge dropped the logs' skipped records, and a skipped transcript's records were never counted. (#938)
