### Fixed

- **`extract_json_object` always returns an object** — a judge or agent reply
  that parsed to another JSON value (`7`, `[1, 2]`, `"x"`, `null`) came back as
  that value, so the judge's retry on a malformed reply never saw it and the
  call failed with an `AttributeError` at `.keys()`. It now raises `ValueError`
  like any reply with no JSON object, and is retried; so does JSON nested past
  the recursion limit, in `extract_json_array` too. Found by the new fuzzing.
- **Frontmatter with a value that does not fit its tag** — `date: 2026-02-30`,
  a `!!int` with no value or `!!bool x` made `parse_frontmatter` and
  `parse_frontmatter_strict` raise `ValueError`, `AttributeError`,
  `IndexError` or `KeyError`. `parse_frontmatter` now returns `{}` and the
  body, and `parse_frontmatter_strict` raises `yaml.YAMLError`.
- **`json` reference check on deeply nested JSON** — an answer nested past the
  recursion limit raised `RecursionError`; the check now fails with
  `(False, None)`.

### Added

- **Fuzzing** — atheris targets for the parsers of model replies and Markdown
  frontmatter (`fuzz/`), run by the `fuzz` workflow on pull requests that touch
  them and weekly; the seed corpus replays in every test run.
