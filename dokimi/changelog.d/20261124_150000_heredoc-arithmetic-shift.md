### Fixed

- **A `<<` shift inside `$((...))` opens no heredoc** — `echo $((1<<3))` followed by `cat AGENTS.md` opened a heredoc no line closed, so the unclosed-heredoc rule cut away `cat AGENTS.md` in `context/shell.py` and the trace reader. A `<<` inside `$((...))` is no opener, as one inside a quote is not; a `$((` with no `))` after it blanks nothing (#930).
