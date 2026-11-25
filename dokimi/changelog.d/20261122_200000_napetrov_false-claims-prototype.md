### Added

- **False-claims research prototype** (`dokimi.novelty.false_claims`) — lists the sentences
  of a stored agent answer that contradict the product's documented atoms, with the atom,
  its span ids and a verbatim quote; sentences no atom covers are counted as unchecked.
  The lexical check handles compact durations, flags whose case differs, and
  dotted API identifiers with digits. The module is a library prototype.
