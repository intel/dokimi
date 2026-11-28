### Fixed

- **A `#` after `;`, `&`, `|` or a parenthesis starts a comment, and a `<<` in a bare `((...))` is a shift** — `echo hi;# usage: cat <<EOF` opened a heredoc in its comment, and `((x=1<<3))` or `for ((i=0; i<1<<3; i++))` read its shift as one, so the unclosed-heredoc rule cut away the next command (#930).
