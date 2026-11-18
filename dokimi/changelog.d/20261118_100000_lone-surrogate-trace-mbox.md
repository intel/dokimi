### Fixed

- **A trace whose command output holds a lone surrogate no longer stops `repo run` or `events.convert`** — a `\ud800` or `\udcff` escape in the output of a command (a tool result of a Claude Code trace, or the output of a shell command in another harness trace) raised `UnicodeEncodeError` in `clip_output`. The stored output has the lone surrogate replaced by `?`, so it stays valid UTF-8; a JSON-style surrogate pair (a high escape followed by a low one) is joined into its character first; the output of a command whose text encodes as UTF-8 is unchanged.
