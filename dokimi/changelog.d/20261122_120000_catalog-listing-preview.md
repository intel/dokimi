### Added

- **`catalog check` rows show the listing preview.** Each skill row has a `listing` object with the text `repo budget` lists, its source (`frontmatter` or `body`) and its character charge, including for a skill whose missing `description` makes it fail to load (#1087).
- **`catalog check` counts a skill without `description` in `listing_chars`.** A skill that fails to load only for a missing `description` is listed by its body's first line, so its row and the listing total carry that charge (#1087).
- **`catalog novelty` refuses `--jsonl` or `--summary-out` onto a skill's `novelty.json` or `novelty.md` under `--out-dir`,** also through a symlinked skill directory: the summary would replace the paid artifact, or the sweep would delete the open JSONL.

### Fixed

- **`repo run` choices: `poetry add --lock` is no install.** It rewrites the lock file and installs nothing, so it gives no install choice.
