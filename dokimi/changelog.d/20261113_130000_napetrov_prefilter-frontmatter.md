### Fixed

- **`trace prefilter` no longer reads YAML frontmatter as a rule** — a guidance file that opens with
  frontmatter (a Claude Code memory file, a `SKILL.md`) had the whole `---` block, `name:` and
  `description:` lines included, counted as its first claim, so the report ranked metadata as a
  candidate for ablation. The opening block is now skipped; a `---` rule later in the file is untouched (#728).
