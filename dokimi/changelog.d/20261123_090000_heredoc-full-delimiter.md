### Fixed

- **A heredoc delimiter is read whole, and a here-string is no heredoc** — the heredoc scans of the trace reader and of `context/shell.py` took only the word characters of `<<EOF-END` as the delimiter, so no line closed it and the unclosed-heredoc rule cut away the commands after the real closing line, and `cat <<<word` opened a heredoc at its second `<` and cut away everything after it. The delimiter is now the whole shell word (`EOF-END`, `EOF.d`, `EOF/END`, up to a space, quote, backslash or one of `;&|<>()`), and a `<<` after a `<` opens nothing (#930).
