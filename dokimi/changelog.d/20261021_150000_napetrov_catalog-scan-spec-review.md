### Added

- **`catalog scan` reviews each skill against the Agent Skills spec** — a loaded skill
  whose `name` is not its directory name or not kebab-case (at most 64 characters), or
  whose `description` is over 1024 characters, is an `error`; a `name` missing from the
  frontmatter, or a `SKILL.md` byte-identical to another skill's (a mirror, named with
  the copy nearest the root, the one the `skill-catalog:` arm offers), is a `warn`. `disable-model-invocation: true` is printed as a
  note, since the model is never offered that skill. Each finding is printed under its
  skill.
- **`catalog scan --strict`** — exits 1 on a skill spec error as well as on a skill that
  does not load. Without it a spec error is printed and the exit code stays 0.
- **`catalog scan --format json`** — one object with a row per skill (`state` is
  `ok`, `warn` or `error`, `findings`, `content_sha256`, `duplicate_of`) and per task
  (`state` is `ok`, `skipped` or `error`), for a CI gate or a script.

### Fixed

- **`catalog scan` names broken YAML frontmatter** — a `SKILL.md` whose frontmatter is not
  valid YAML (a bare colon in `description:`, say) is reported as a `frontmatter-parse`
  error with the YAML message, and exits 1. The scan showed only "missing a description"
  before. `parse_frontmatter` is unchanged for other callers.
- **`catalog scan` without a `skills.yaml` no longer stops at the first broken skill** — each
  `SKILL.md` under the root is an entry, so a skill that does not load is an error row and
  the rest are still reviewed. A `SKILL.md` under `template/` or `templates/` is still left out.
