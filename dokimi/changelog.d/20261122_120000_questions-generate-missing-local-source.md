### Fixed

- **`eval questions generate` refuses a `--doc-source local:<path>` or `okf:<path>` that does not exist.** It exits 2 with `Error: ... path does not exist: <path>` before extracting topics. Before, it logged the failed read, fell back to default seed topics, saved an empty question set and exited 0.
