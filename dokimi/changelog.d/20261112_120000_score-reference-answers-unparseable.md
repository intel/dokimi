### Fixed

- **`eval score reference` refuses an unparseable `--answers` file** — an empty, non-JSON or non-UTF-8 `--answers` file (also a missing file or a directory) now exits 2 with `Error: --answers <path>: <reason>`, as `eval leak` and `eval score checklist` do; it ended in a traceback and exit 1. An unparseable `--questions` file still does, until `load_questions` takes the flag name.
