### Fixed

- **`python -m dokimi.schema_compat` compares a schema whose file name git quotes, and runs
  in a repository whose root has a carriage return in its name** — it listed the base's
  schemas with `git ls-tree` without `-z`, which prints a name holding a carriage return,
  a `"` or a non-ASCII character in C quotes, so that schema did not end in `.json`, was
  skipped and a breaking edit to it passed; a space cut a name in two. A repository root
  whose name holds a carriage return, ends in one or in a space, or is not valid UTF-8 made
  the check stop with a `ValueError` whenever the base had no schemas at their current path,
  as after a move. The listings are now split on NUL, the root is read from git's raw output
  less its final newline, and each path is decoded with `os.fsdecode`, so a name that is not
  valid UTF-8 still names its file; the check prints such a name with a `?` for the byte.
