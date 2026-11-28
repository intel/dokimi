### Fixed

- **`repo security` reads a word of repeated path parts in linear time.** The `secret-read` rule read the rest of a word again from each `/`, so a word like `a/a/a/...` of 3000 parts took quadratic time. It now reads a word from its first `/`, and finds the same lines (#688).
