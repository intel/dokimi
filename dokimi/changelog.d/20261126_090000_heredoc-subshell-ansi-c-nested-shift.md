### Fixed

- **A heredoc opened in `((cat <<EOF` or after `$'don\'t'` is cut, and a nested shift on a substitution line opens none** — a bare `((` is arithmetic only when a `))` closes it, so `((cat <<EOF ... ) | cat)` keeps its subshell and loses its body; an escaped quote in an ANSI-C string no longer opens a quote that hides the next heredoc; and in `$(( $(echo $((1 << EOF))` the lookahead over the substitution line reads the nested `$((` as arithmetic, so the shift names no delimiter and the commands after the span are kept (#930).
