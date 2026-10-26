### Fixed

- **The novelty report no longer shows an unchecked 0 as a clean one** — when
  the `harmful_atoms` gate check reads a count of 0 conflicting atoms as
  `not measured` (the conflict check compared fewer pairs than
  `min_conflict_coverage`, the atom set was capped, or `conflict_coverage` is
  malformed), the Markdown report `novelty analyze` writes now says why in
  place of the `Conflicts first` line, and its `harmful_conflicting` row reads
  `not measured (0 found)`. Before, it printed `| harmful_conflicting | 0 |`
  next to a gate row that said `not measured`.
