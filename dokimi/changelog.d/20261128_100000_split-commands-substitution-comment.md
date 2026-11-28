### Fixed

- **An apostrophe in a `#` comment inside `$(...)` or backticks no longer hides the commands after it** — `split_commands` keeps such a comment, up to the end of its line, apart from the quotes and parentheses it reads, so `echo $(echo 1 # it's` followed by `)` and `cat AGENTS.md` on later lines gives both commands, and a `)` in the comment does not close the substitution; the same holds for the masking that finds a command's parentheses (#1236).
