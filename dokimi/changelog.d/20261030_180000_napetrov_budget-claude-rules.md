### Added

- **`repo budget` counts `.claude/rules`** — every `.claude/rules/**/*.md` file is a `rule` entry.
  A rule in the root `.claude/rules/` with no `paths` frontmatter is `start`; one with `paths`, and
  every rule of a subdirectory's `.claude/rules/`, is `lazy`. The frontmatter is not counted, since
  Claude Code removes it before loading the rule, and an `@` path in a rule is not followed as an
  import. Measured on Claude Code 2.1.304: a root rule raised the first prompt by its size, a scoped
  or nested one did not, and a rule's `@extra.md` was not expanded. `start` was too low by
  every root rule before; `repo_budget.v1` widens `kind` with `rule`. Rules are counted for a
  harness profile with the new optional `guidance.rules: true` (`claude-code` only): Codex and
  Copilot do not read `.claude/rules`.
