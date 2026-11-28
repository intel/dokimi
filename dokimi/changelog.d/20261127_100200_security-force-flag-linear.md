### Fixed

- **`repo security` reads a long flag word after `git push` in linear time.** The force flag of `history-rewrite` backtracked over a long run of letters with an `f` that ends before a digit or `_`, so `git push -` and 3000 `f` and a `1` took quadratic time. It now takes the word whole and finds the same lines (#1268).
