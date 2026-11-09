### Added

- **`catalog check` says how to turn a plain-Markdown `SKILL.md` into a skill.** A `SKILL.md` with no frontmatter still fails to load, and now has one finding, `not-a-skill-no-frontmatter`, whose message holds a `name`/`description` block made from the file's H1 and first paragraph. The summary line counts these apart from the other errors (`3 error, of them 2 plain Markdown with no frontmatter`), so a scan of a large corpus can tell prose files from broken skills.
