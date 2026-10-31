### Added

- **`repo budget` counts `.claude/rules`** — a `.claude/rules/**/*.md` file is a `rule` entry.
  A rule in the root `.claude/rules/` whose `paths` frontmatter names no pattern is `start`; one
  whose `paths` does, and every rule of a subdirectory's `.claude/rules/`, is `lazy`. The
  frontmatter is not counted, since Claude Code removes it before loading the rule, and an `@`
  path in a rule is not followed as an import. A link to a directory under `.claude/rules` is a
  warning: the rules behind it are not counted. Measured on Claude Code 2.1.304: a root rule
  raised the first prompt by its size, `paths: []` included; a scoped or nested one loaded only
  once the agent read a file it covers; a rule's `@extra.md` was not expanded. `start` was too
  low by every root rule before; `repo_budget.v1` widens `kind` with `rule`. Rules are counted
  for a harness profile with the new optional `guidance.claude_rules: true` (`claude-code`
  only); the Codex and Copilot profiles leave it unset.
