### Added

- **`skill novelty analyze --estimate-only` names each role's dollars and the role its figure is least sure of** — the payload gains `usd_by_role` (extractor, answerer and judge dollars, summing to `expected_usd`; panel judges count under `judge`) and `least_sure_role` (the role and a reason), both `null` when `expected_usd` is; stderr prints one line with that role and its share. `study` and an untiered run name the extractor, which borrows the standard profile; `triage` and `standard` name the role with the largest dollars. `expected_usd` and the notes are unchanged.
