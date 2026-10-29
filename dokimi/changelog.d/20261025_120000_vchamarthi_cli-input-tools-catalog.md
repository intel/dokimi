### Fixed

- **`tools preference` and `catalog`** exit with a message on a non-UTF-8 `--groups` file, an unwritable `--out`, `--items-out` or `--out-json`, an unreadable or wrong-shaped manifest, a malformed `SKILL.md` under `--catalog`, or a task directory without `instruction.md`, instead of a traceback.
