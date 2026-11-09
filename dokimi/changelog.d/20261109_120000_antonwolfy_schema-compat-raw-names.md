### Fixed

- **`python -m dokimi.schema_compat` compares a schema whose file name holds a carriage
  return, a quote, a space or a non-ASCII letter, and runs in a repository whose root path
  holds a carriage return** — it listed the base's schemas without `-z`, so git quoted such
  a name, which then did not end in `.json` and was skipped (a breaking change to it
  passed), and a name with a space was split in two and stopped the check with a git
  error. It also read the repository root as text, which turns a `\r` into a newline, so
  following moved schemas stopped with a `ValueError`. The listings now use `-z` and are
  split on NUL, and the root is decoded from git's bytes with only its trailing newline
  dropped. When git prints no root (git before 2.25 outside a work tree), the check stops
  with a git error that says so, not a `ValueError`.
