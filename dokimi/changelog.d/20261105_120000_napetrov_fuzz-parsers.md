### Fixed

- **`extract_json_object` always returns an object** — a judge or agent reply
  that parsed to another JSON value (`7`, `[1, 2]`, `"x"`, `null`) came back as
  that value, so the judge's retry on a malformed reply never saw it and the
  call failed with an `AttributeError` at `.keys()`. It now raises `ValueError`
  like any reply with no JSON object, and is retried. Found by the new fuzzing.

### Added

- **Fuzzing** — atheris targets for the parsers of model replies and Markdown
  frontmatter (`fuzz/`), run by the `fuzz` workflow on pull requests that touch
  them and weekly; the seed corpus replays in every test run.
