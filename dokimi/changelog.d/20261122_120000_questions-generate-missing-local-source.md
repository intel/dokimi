### Fixed

- **`eval questions generate` refuses a `--doc-source local:<path>` or `okf:<path>` that does not exist.** It exits 2 with `Error: doc source path does not exist: <source>` before extracting topics. Before, it logged the failed read, fell back to default seed topics, saved an empty question set and exited 0. `benchmark check` shares the same check, so a path that exists but cannot be read (a file without read permission, or a directory without read and search permission) is reported as unreadable there too.
