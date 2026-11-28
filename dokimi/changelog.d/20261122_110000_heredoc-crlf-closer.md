### Fixed

- **The trace reader closes a heredoc at a closing line that ends in a carriage return** — a command with CRLF line ends (`cat <<EOF`, `x`, `EOF`) kept the body line as a command, because the closing-line pattern did not take the `\r`; with an unclosed heredoc running to the end of the input it would have swallowed every line after it. `context/shell.py` already read such a closing line (#930).
