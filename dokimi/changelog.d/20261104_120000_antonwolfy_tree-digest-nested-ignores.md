### Fixed

- **A nested repository's own ignore rules now apply to `skill_sha` and `suite_hash`** — in a git work tree, a
  file that a nested repository or submodule inside a skill or suite ignores (its `.gitignore`, `info/exclude`)
  leaves both hashes, as a file the top repository ignores does; its tracked and untracked files still count, and so
  does a file the top repository tracks there.
  A file whose name holds a newline, starts with `"` or ends with a carriage return now digests by its blob id
  whether it is committed or edited to the same content (it was hashed by its raw bytes once edited), so it
  digests the same both ways. Either change moves the hash of a tree that holds such files, once.
