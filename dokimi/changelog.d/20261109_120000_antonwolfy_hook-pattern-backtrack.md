### Fixed

- **`repo scan` no longer stalls on a `no-commit-to-branch` pattern that backtracks exponentially** — a hook
  `--pattern` such as `maintenance/(a+)+b` was matched with `re` against each path-like claim of the guidance,
  and a claim like `maintenance/aaaaaaaaaaaaaaaaaaaaaaaac` took about twice as long for each extra `a`. Such a
  pattern is now refused before any claim is matched against it, as an invalid regex is: it names no branch and
  the claim keeps its path verdict. Refused is a repeat that can run more than once around a repeat whose count
  varies, or around an alternation whose branches can start with the same character or match nothing
  (`(a+)+`, `(a*)*`, `(a{1,5})+`, `(a|aa)+`); other slow shapes still pass. The rule is in
  `docs/repo-evaluation-contract.md`.
