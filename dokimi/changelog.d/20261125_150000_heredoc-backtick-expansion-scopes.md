### Fixed

- **A `<<` in a backtick substitution, or after a `${...}` holding a `#` or a heredoc inside a quoted `$(...)`, no longer hides the commands after it** — in `echo "`printf "%s" "<<EOF"`"` the quotes in the backticks are their own, as in `$(...)`; a `#` in `${x:- #}` starts no comment, so the heredoc after it is read as one; and the quotes around a `$(cat <<EOF ...)` stay open past the heredoc body, so a later `((x=1<<3))` is still arithmetic. A line continuation chain before a comment is read in a loop, not by recursion, so 1500 of them no longer raise `RecursionError` (#930).
