### Fixed

- **A `<<` inside a quote that spans lines no longer opens a heredoc** — in `python -c "` / `x = 1 << 3` / `"` and in a multi-line `git commit -m '…<<EOF…'`, the quote was reset at each newline, so the `<<` opened a heredoc that cut away the commands after it. A quote now runs to its closing mark over lines, and the body of a heredoc is skipped as data, so an apostrophe in it hides no later `<<` or `$((` (#930).
