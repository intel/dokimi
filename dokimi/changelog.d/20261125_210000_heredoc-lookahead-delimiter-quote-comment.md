### Fixed

- **A heredoc-producing substitution in arithmetic is read whole when its line has a literal delimiter, a quote or a comment** — in `echo $((1 << 3 + $(cat <<"${" ...)))` the delimiter's `"` and `${` no longer read as syntax in the lookahead for the closing `))`, and in `$(cat <<EOF >/dev/null; printf '%s' 0` or `... # note` the opener stays known after the quote or comment, so the body is skipped, the shift opens no heredoc, and the commands after the span are kept (#930).
