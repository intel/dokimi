### Added

- **`skill novelty view --atom-labels FILE`** — joins your labels per atom (JSON lines, or CSV with `skill`, `atom_id` and `label` columns) to the `--atom-verdicts` of `skill novelty analyze` and prints the known and new shares per label and for the whole run. Needs `--atom-verdicts`; nothing is written back to the artifacts.
