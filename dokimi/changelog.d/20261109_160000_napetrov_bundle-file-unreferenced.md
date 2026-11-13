### Added

- **`catalog check` counts bundled files the skill never names.** A script or document next to `SKILL.md` that neither `SKILL.md` nor a document it names mentions (by path, file name or folder, case ignored) is counted in one new `warn` per skill, `bundle-file-unreferenced`, with the count and the first three paths. Data, images, licence and readme files, `_`-prefixed helpers, tests and vendored folders are not counted. Leave the code out with `--ignore bundle-file-unreferenced`.
