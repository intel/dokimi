### Fixed

- **`repo` commands refuse a `$DOKIMI_REPO_CACHE` that is not a usable directory with exit 2 and a message naming the variable and the path** — with no `--cache-dir`, a variable naming a regular file (or a path below one) used to end in `✗ [Errno 17] File exists` and exit 1 at the clone; it is now checked before any clone, as `--cache-dir` is. A `--cache-dir` that is given wins, and the variable is not looked at then. A bad variable next to a local-directory source is refused too, where it used to be ignored.
