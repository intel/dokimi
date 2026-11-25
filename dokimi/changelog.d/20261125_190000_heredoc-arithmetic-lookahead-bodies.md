### Fixed

- **A shift before a heredoc-producing substitution in arithmetic, a bare `((...))` in a substitution in arithmetic, and a long unclosed arithmetic expansion are read as before** — in `echo $((1 << 3 + $(cat <<EOF ...it's...EOF )))` the lookahead for the closing `))` skips the heredoc body of the substitution, so its apostrophe no longer hides the end of the span and the shift opens no heredoc; `$( ((x=1<<3)); ...)` inside `$((...))` is arithmetic, not a heredoc opener; and an unclosed `$((` followed by many `<<` openers reads the text after it once, not once per opener (#930).
