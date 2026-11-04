### Fixed

- **`novelty routing --out` is checked before any model call** — a directory, a
  path under a non-directory or an unwritable path is refused up front for both
  the single-artifact run and `--all-vs-all`. Before, `--out <directory>` made
  every call and then crashed with `IsADirectoryError`, and nothing was saved. A
  missing parent directory is still created. Pass a file path.
- **A `SKILL.md` with broken frontmatter reports the YAML error** — the message
  names the file line and says to quote a value containing `: `, in place of
  "missing a 'description'". It shows in `catalog scan` and in `novelty routing`
  (`--artifact`, `--competitors`, `--catalog`).
- **`novelty routing --estimate-only` prices the reply it measured** — output
  tokens are 65 a call (the average of a measured run, 23.3k over 360 calls), not
  one JSON pick (10). The JSON output has `output_tokens_per_call`.

### Changed

- **`noise` is always an object in routing and `--all-vs-all` results** — with
  `--repeats 1` it is no longer `null`: `n_repeats` is 1, and the agreement, mean
  agreement and half-range fields are `null`. The console `noise` line prints only
  for two or more repeats. Code that tested `noise` for `null` should test
  `noise["n_repeats"] > 1`.
