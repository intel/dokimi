### Fixed

- **Commands on a directory without search permission refuse it with a message.** `eval check --schema-only` on a plain directory of mode 444 ended in a `PermissionError` traceback (exit 5), with or without `--manifest-file`. It now prints that the directory cannot be searched and exits 2 (#1293).
