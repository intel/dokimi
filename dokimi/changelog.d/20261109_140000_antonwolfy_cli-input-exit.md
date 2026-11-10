### Fixed

- **`trace prefilter` reads a session log through a pipe whole** — a log passed as
  `<(cat session.jsonl)` or `/dev/fd/<n>` lost its first 16 bytes to the Goose store check, so a
  readable log was reported as `no prompt and no tool call` and the command exited 1. Only a
  regular file is checked for the SQLite header now; anything else is read as a JSONL log.
- **`eval tasks generate --from replay` and `--from prs` exit 2 on a source refused as typed** —
  a malformed source, an unpinned remote or a local source that is not a directory exited 1, the
  code for an unusable pull request file or a source that cannot be opened, while `--from graph`
  exited 2. All three now exit 2 with the same `Error:` line, before the threads or pull request
  file is read.
