### Fixed

- **`repo budget --harness copilot` (and `copilot-review`) no longer reports a root `AGENTS.md`
  as `off` beside a root `CLAUDE.md`** — the rule that a root `CLAUDE.md`, `CLAUDE.local.md` or
  `.claude/CLAUDE.md` turns every `AGENTS.md` off is Claude Code's, but `repo budget` applied it to
  any profile whose guidance names list `CLAUDE.md`, and Copilot's do; Copilot CLI 1.0.91 reads
  both files. The root rule is now gated, as the same-directory rule already was, on the profile
  key `guidance.claude_displaces_agents`, which only `claude-code` sets: with any other profile no
  `AGENTS.md` is `off` and the "turns AGENTS.md loading off" warning is gone, so
  `repo budget --fail-on-warning --harness copilot` on a tree with a root `CLAUDE.md` and an
  `AGENTS.md` exits 0 where it exited 1: a CI gate on that exit code passes now. `@` imports are now
  followed only for a profile that declares an import syntax (`guidance.imports`, `@path` for
  `claude-code`): `codex`, `codex-review`, `copilot` and `copilot-review` declare none, so no file
  an `AGENTS.md` or `CLAUDE.md` imports is counted for them. `claude-code` budgets do not change.
  `repo receipt` gives an expected state only for `claude-code`, so its output does not change either.
