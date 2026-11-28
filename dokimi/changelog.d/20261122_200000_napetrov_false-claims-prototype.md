### Added

- **False-claims research prototype** (`dokimi.novelty.false_claims`) — lists the sentences
  of a stored agent answer that may contradict the product's documented atoms, with the atom,
  its span ids and a quote: without a judge these are low-precision lexical candidates, with
  a judge (`client_judge`) sentences it marks contradicting with a verbatim quote; sentences
  no atom covers are counted as unchecked.
  The lexical check handles compact durations, flags whose case differs, and
  dotted API identifiers with digits. The module is a library prototype.
