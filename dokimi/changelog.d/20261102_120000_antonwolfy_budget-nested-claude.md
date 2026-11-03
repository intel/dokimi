### Fixed

- **`repo budget` reports a nested `AGENTS.md` as `off` when its own directory holds a
  `CLAUDE.md`, `CLAUDE.local.md` or `.claude/CLAUDE.md`** — Claude Code 2.1.316 does not read
  `sub/AGENTS.md` beside `sub/CLAUDE.md`, also when it reaches `sub/` lazily from the root, but
  `repo budget` counted it as `lazy`, along with its `@` imports. It is now `off`, its imports are
  not counted, and a warning names the `CLAUDE*` file that turns it off. It stays `lazy` when one
  of those files is a link to it or a loaded file `@`-imports it, or when the same file is also
  reached as an `AGENTS.md` in another directory, and when it does not resolve (a dangling or
  out-of-tree link keeps `lazy` and only the not-readable warning). A `CLAUDE*` file in a parent below the root or
  in a deeper directory does not turn it off; a root one still turns every `AGENTS.md` off. The rule applies only with a profile that sets the new optional
  `guidance.claude_displaces_agents` key of `harness_profile.v1`, which only `claude-code` does: a
  tree with a root `CLAUDE*` file, and the Codex and Copilot profiles (Copilot's guidance names
  list `CLAUDE.md`), get the same budget as before. The `claude-code` profile's `guidance.notes`
  state the measured rule.
