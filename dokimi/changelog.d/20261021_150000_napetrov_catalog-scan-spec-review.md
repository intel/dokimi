### Added

- **`catalog scan` reviews each skill against the Agent Skills spec** — a loaded skill
  whose `name` is not its directory name or not kebab-case (at most 64 characters), or
  whose `description` is over 1024 characters, is an `error`; a `name` missing from the
  frontmatter, or a `SKILL.md` byte-identical to an earlier skill's (a mirror, named
  with its first copy), is a `warn`. `disable-model-invocation: true` is printed as a
  note, since the model is never offered that skill. Each finding is printed under its
  skill.
- **`catalog scan --format json`** — one object with a row per skill (`state` is
  `ok`, `warn` or `error`, `findings`, `content_sha256`, `duplicate_of`) and per task
  (`state` is `ok`, `skipped` or `error`), for a CI gate or a script.

### Changed

- **`catalog scan` exits 1 on a skill spec error** (a name that is not the directory
  name or not kebab-case, a description over 1024 characters), as it already did for a
  skill that does not load. Warnings (mirrors, a missing `name`) do not change the exit
  code. A catalog that declares such a skill now fails the scan; fix the frontmatter
  or drop the skill from the scan.
