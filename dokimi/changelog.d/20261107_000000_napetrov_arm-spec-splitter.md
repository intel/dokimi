### Changed

- **One `+` splitter for arm specs** — `--arms` treatment specs and `repo run --arm`
  specs now share one splitter (`dokimi.treatments.specs.split_parts`). Accepted
  specs are unchanged; the internal parsers that split on `+` are no longer duplicated.
