### Fixed

- **`repo budget --harness copilot` (and `copilot-review`) no longer reports a root `AGENTS.md`
  as `off` beside a root `CLAUDE.md`** — the rule that a root `CLAUDE.md`, `CLAUDE.local.md` or
  `.claude/CLAUDE.md` turns every `AGENTS.md` off is Claude Code's, but `repo budget` applied it to
  any profile whose guidance names list `CLAUDE.md`, and Copilot's do; Copilot CLI 1.0.91 reads
  both files. The root rule is now gated, as the same-directory rule already was, on the profile
  key `guidance.claude_displaces_agents`, which only `claude-code` sets: with any other profile no
  `AGENTS.md` is `off`, its `@` imports are counted, and the "turns AGENTS.md loading off" warning
  is gone. `claude-code` budgets do not change. `repo receipt` gives an expected state only for
  `claude-code`, so its output does not change either.
