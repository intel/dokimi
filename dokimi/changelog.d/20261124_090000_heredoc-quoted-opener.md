### Fixed

- **A `<<` inside a quote opens no heredoc, and a quoted delimiter with a space is read whole** — `grep '<<EOF' x.txt` opened a heredoc no line closed, so the unclosed-heredoc rule cut away the commands after it, and `cat <<'a b'` was read as the delimiter `a`, so its closing line `a b` never closed it. In `context/shell.py` and the trace reader a `<<` inside a quote of its line opens nothing, and a quoted delimiter is read up to its closing quote (#930).
