### Changed

- **The shell walker of the CLI traces moved to its own module** — `unwrap_shell`, `shell_reads`, `shell_reads_moved`, `shell_walk`, `shell_walked` and the heredoc patterns moved from `repos/cli_traces.py` to `repos/shell_walk.py`, so `cli_traces.py` is under the 800-line cap again. `cli_traces` still exports every name. An internal change: the traces, events and reads they give are unchanged.
