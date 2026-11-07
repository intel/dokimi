### Changed

- **A skill whose only spec error is a description over 1024 characters stays in the routing
  field** — `dokimi novelty routing` (single-artifact and `--all-vs-all`) left such a skill out
  unless `--include-invalid` was passed, but harnesses differ: Claude Code lists it and truncates
  the description past about 1,536 characters, pi.dev rejects it. The skill now stays in the
  field, from `--catalog` or `--competitors skill:`, with its full description. The run warns
  with the skill name and its length, and the saved result lists it under `field.spec_warnings`
  (an empty list when none); an over-limit skill under test (`--artifact`) is warned about and
  listed there too.
  Any other spec error (name not kebab-case or not its directory name, broken frontmatter) still
  leaves a skill out unless `--include-invalid`. `dokimi catalog check` no longer treats such a
  copy as unlisted, so it reports a `name-conflict` with a different copy of the same name, as
  routing does.
  The routing field of a catalog with such a skill changes, so routing rates measured before
  this release on it differ: re-run a saved baseline before comparing with it.
