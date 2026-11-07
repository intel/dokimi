### Added

- **`catalog check` reports four more spec and size warnings on loaded skills** — a frontmatter key
  that neither the Agent Skills spec nor Claude Code names is a `warn` (`frontmatter-unknown-key`),
  and so is an `allowed-tools` entry that is not a Claude Code tool (`allowed-tools-unknown`; `mcp__*`
  tools are not checked). A body over 500 lines or about 5,000 tokens (characters / 4) is a `warn`
  (`body-too-large`). A `SKILL.md` with no frontmatter now fails to load with "no frontmatter", and one
  with an unclosed `---` block with "unclosed frontmatter", where both said "missing a `description`".
  The `name-format` error for a name over 64 characters now says the length. Warnings do not change the
  exit code; no skill has to change to keep passing `catalog check`.
