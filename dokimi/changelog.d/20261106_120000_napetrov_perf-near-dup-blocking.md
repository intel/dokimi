### Changed

- **`catalog check` finds near-duplicate descriptions in far less time on large catalogs** — the
  `description-near-duplicate` pass compares only the skill pairs that can reach its threshold
  (a prefix filter over the description words), instead of every pair that shares a word. Findings are
  unchanged. On a synthetic 5000-skill catalog with a realistic word tail the pass went from about 16 s to under 1 s; nothing to do on upgrade.
