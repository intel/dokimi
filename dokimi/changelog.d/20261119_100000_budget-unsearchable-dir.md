### Fixed

- **`repo budget` on a git working tree no longer ends in a `PermissionError` traceback when a tracked file is under a directory that cannot be searched** — `WorkTree.files` looked at every path git listed and let the error out. A path that cannot be looked at is now left out of the listing and named in the budget's `warnings` (`<path>: Permission denied; the file and anything it leads to are not counted`), so `--fail-on-warning` exits 1 on it and `repo scan --fail-on-fail` reports the scan as incomplete, and the table is printed for the rest. (#959)
