### Added

- **Novelty warns when the extractor and the judge are one model or one vendor family.** The
  extractor writes the answer key and the judge grades against it, so one model doing both
  is flagged on stderr by `novelty analyze`, and by `novelty probe` for the model that wrote the
  saved answer key. `novelty analyze` records the relation in `run_config.extractor_judge`
  (`extractor`, `judge`, `same_model`, `same_vendor`). The run is
  not refused and no default changes: with no `--extract-model` the extractor is still the judge,
  so a default run now warns. `docs/skill-novelty.md` has a new section, "Choosing the extractor
  and the judge", on picking a cross-family pair.
