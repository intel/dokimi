### Fixed

- **A double-quoted `$((...))` no longer hides the next arithmetic expansion's shift from the heredoc scan** — in `echo "$((1<<3))"` followed by `echo $((1<<3))` and `cat AGENTS.md`, the quote around the first expansion stayed open inside it, so its `))` were not balanced and the second `<<3` opened a heredoc that cut away `cat AGENTS.md` (#930).
