### Fixed

- **A `<<` after a line continuation and a blank, or in the quotes of a `$(...)`, no longer opens a heredoc** — `echo hi \` newline `# usage: cat <<EOF` is a comment once the shell joins the lines, and in `echo "$(printf "%s" "<<EOF")"` the quotes inside `$(...)` are their own, apart from the double quote around it; both opened a heredoc that cut away the commands after them. A `#` now starts a comment after a continuation that follows a blank, and a quote stack keeps each `$(...)` scope apart (#930).
