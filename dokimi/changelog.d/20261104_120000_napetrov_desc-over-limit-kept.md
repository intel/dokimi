### Changed

- **A skill whose only spec error is a description over 1024 characters stays in the routing
  field** — `dokimi novelty routing` (single-artifact and `--all-vs-all`) left such a skill out
  unless `--include-invalid` was passed, but harnesses differ: Claude Code lists it with the
  description cut short, pi.dev rejects it. The skill now stays in the field, from `--catalog` or
  `--competitors skill:`, with its full description. The run warns with the skill name and its
  length, and the saved result lists it under `field.spec_warnings` (an empty list when none).
  Any other spec error (name not kebab-case or not its directory name, broken frontmatter) still
  leaves a skill out unless `--include-invalid`. `dokimi catalog scan` no longer treats such a
  copy as unlisted, so it reports a `name-conflict` with a different copy of the same name, as
  routing does.
