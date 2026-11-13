### Fixed

- **`eval score reference` refuses an unparseable `--answers` file** — an empty, non-JSON or non-UTF-8 `--answers` file (also a missing file or a directory) now exits 2 with `Error: --answers <path>: <reason>`; it ended in a traceback. `eval score checklist` also exits 2 on such an `--answers` file, with its own message; `eval leak` takes no `--answers`. An unparseable `--questions` file for `eval score reference` still ends in a traceback and exit 1, until `load_questions` takes the flag name.
