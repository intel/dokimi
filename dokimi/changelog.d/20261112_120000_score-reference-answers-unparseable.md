### Fixed

- **`eval score reference` refuses an unparseable `--answers` file** — an empty, non-JSON or non-UTF-8 `--answers` file (also a missing file or a directory) now exits 2 with `Error: --answers <path>: <reason>`; it ended in a traceback and exit 1. `eval leak` and `eval score checklist` also exit 2 on such a file, with their own messages. An unparseable `--questions` file still ends in a traceback and exit 1, until `load_questions` takes the flag name.
