### Fixed

- **Qualified and negated figure verdicts** — the wrong-figure cap in
  `Judge._is_wrong_figure()` now reads a verdict that ends the claim after a
  qualifier list or trailing clause, such as `x: misleading/wrong` or
  `x: reasonable, not wrong`. Such claims were unread before, so a figure the
  judge called wrong did not cap `correctness` at 40. `not right` and
  `not correct` now cap too. A `/` or `,` is not a separator on its own
  (`x: see /wrong` and `64 bytes, wrong` stay unread), alternatives such as
  `right/wrong` stay unread, and adverbs other than intensifiers
  (`hardly wrong`, `possibly wrong`) leave the claim unread.
