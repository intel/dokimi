### Added

- **`catalog check` reads bundled file paths in code spans.** A code span naming a file under `scripts/`, `references/`, `assets/`, `templates/`, `examples/` or `resources/` (alone, or in a command such as `python scripts/run.py`) that the skill does not have is a new `warn`, `path-missing`. Before, only Markdown links were checked (`link-missing`), and most bundle references are written in backticks. Folders, flat names, placeholders and repo-root paths are not checked. Leave the code out with `--ignore path-missing`.
