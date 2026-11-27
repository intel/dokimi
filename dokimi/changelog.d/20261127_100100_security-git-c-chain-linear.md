### Fixed

- **`repo security` reads a chain of `git -C push`, `-C reset` or `-C commit` in linear time.** The `history-rewrite` and `hook-bypass` rules read the rest of the line again for each `push`, `reset` or `commit` word of such a chain, which `-C` can take as its argument or leave as the subcommand. They now read it from the first of them only, and find the same lines (#1267).
