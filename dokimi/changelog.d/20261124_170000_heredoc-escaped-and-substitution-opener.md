### Fixed

- **An escaped `<` and a `$(...)` delimiter open no heredoc, and closing lines are found in linear time** — `echo \<< /dev/null` and `cat <<$(printf EOF)` opened a heredoc no line closed, so the unclosed-heredoc rule cut away the commands after them in `context/shell.py` and the trace reader. A `<<` whose first `<` follows a backslash, and a delimiter word cut by a `(`, are no opener. Delimiters that share a core (`<<'  a'`, `<<' a'`, ...) are no longer each tried against every line (#930).
