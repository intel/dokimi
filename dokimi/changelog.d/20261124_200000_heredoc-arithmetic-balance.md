### Fixed

- **A `$((...))` ends at its balancing parenthesis, and a `$((` in single quotes or a comment starts none** — `echo $(( (1 + (2)) << 3 ))` ended its arithmetic at the first `))`, so the shift opened a heredoc and the unclosed-heredoc rule cut away the next command; `echo '$(('` and a later `echo '))'` made the heredoc between them read as arithmetic and left its body as commands. Closing lines made of blanks of many lengths are no longer compared at every position of every length (#930).
