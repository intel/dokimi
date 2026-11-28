### Fixed

- **A heredoc delimiter with a backslash opens no heredoc** — `cat <<EOF\-END` was read as the delimiter `EOF` followed by `\-END`, so no line closed it and the unclosed-heredoc rule cut away the real closing line and the commands after it in `context/shell.py` and the trace reader. A delimiter word followed by a backslash is no longer an opener, so the commands after it stay (#930).
