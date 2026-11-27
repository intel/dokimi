### Fixed

- **An escaped blank before a `#` starts no comment, and a quoted `)` inside `$((...))` does not end it** — `echo a\ # <<EOF` kept its heredoc open but read the `#` as a comment, so the heredoc body ran as commands, and `$(( $(printf '))'; echo 1) << 3 ))` ended its arithmetic early and read the shift as a heredoc that cut away the next command (#930).
