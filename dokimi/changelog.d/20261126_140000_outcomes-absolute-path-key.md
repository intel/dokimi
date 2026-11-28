### Fixed

- **`trace consistency --outcomes` matches a key written as an absolute path to a trace named by a relative path** — such a key matched nothing, so every trace was left unjudged. A key now matches when it resolves to the same file; two keys for one trace with different verdicts are refused. `docs/run-consistency.md` also names the test action by kind (`Bash:pytest`) so `command -v pytest` no longer counts, and its verdict table lists the strings the command prints (#1314).
