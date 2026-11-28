### Fixed

- **A bare `((x=1<<3` closed by `))` on a later line is arithmetic, not a heredoc** — a `((` is arithmetic when the parenthesis closing its inner `(` is followed at once by the one closing its outer, on the same line or a later one, so the commands after `((x=1<<3` and `))` on the next line are kept, while `((cat <<EOF ... ) | cat)` stays a subshell (#930).
