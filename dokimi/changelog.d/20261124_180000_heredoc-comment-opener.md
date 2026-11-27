### Fixed

- **A `<<` inside a `#` comment opens no heredoc** — `# usage: cat <<EOF` followed by `cat AGENTS.md` opened a heredoc no line closed, so the unclosed-heredoc rule cut away `cat AGENTS.md` in `context/shell.py` and the trace reader. A `#` at the start of a word outside a quote starts a comment to the end of its line, and a `<<` in it is no opener; `a#b` and `$#` start none (#930).
