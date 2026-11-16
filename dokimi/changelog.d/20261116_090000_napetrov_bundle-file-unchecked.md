### Added

- **`catalog check` says when it does not count bundled files.** When the search cannot see every name (over 2000 files, more than 50 named documents, a named document over 1 MiB (the note names it as truncated), not UTF-8, a symlink or a FIFO, or a folder that cannot be listed), `bundle-file-unreferenced` gives no count, as before, and a new `note`, `bundle-file-unchecked`, gives the reason. Leave the note out with `--ignore bundle-file-unchecked`.
