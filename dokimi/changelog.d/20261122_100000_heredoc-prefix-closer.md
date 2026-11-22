### Fixed

- **An unclosed heredoc is no longer closed by a line naming a prefix of its delimiter** — the heredoc scans of the trace reader and of `context/shell.py` let the regex retry a shorter delimiter, so `cat <<AB` followed by a line `A` was cut as if `AB` had closed it and the lines after it were read as commands. The delimiter word is now atomic: only a line holding the whole delimiter closes a heredoc, and a heredoc with no such line is left uncut. Every input with a closing line is cut as before (#930).
