### Changed

- **Internal: the flag contract also checks what a flag means, not only its spelling.** `tests/test_cli_flags.py` compares every use of a registered spelling with its registry row: switch or value, metavar, choices (including enumerated sets), and the type of `int`, `float` and `usd` rows. The known clashes (for example `--judge` as a switch in `arms run`) are listed with a reason and do not change the CLI. `--context-framing` is declared as a choice in the registry. No command, flag, default or help text changes.
