### Fixed

- **`repo budget` on a git working tree no longer ends in a `PermissionError` traceback when a tracked file is under a directory that cannot be searched** — `WorkTree.files` looked at every path git listed and let the error out. A path that cannot be looked at is now left out of the listing and named in the budget's `warnings` (`<path>: Permission denied; the file and anything it leads to are not counted`), so `--fail-on warn` exits 1 on it and `repo scan --fail-on fail` reports the scan as incomplete, and the table is printed for the rest. (#959)
  `repo scan` counts such a path as a reason the scan is incomplete only when it is guidance the scan would check
  (a guidance glob, `also_scan`, a rule, a Markdown file the default guidance imports, a directory that could not be
  listed, a Bazel build input when the Bazel adapter is on); any other skipped path is only a warning. Outside git the
  directory `os.walk` could not list is named the same way, and a task workspace export of such a tree raises `PrepError`
  instead of copying a partial repository.
