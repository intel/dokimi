### Fixed

- **`repo budget` reports a nested `AGENTS.md` as `off` when its own directory holds a
  `CLAUDE.md`, `CLAUDE.local.md` or `.claude/CLAUDE.md`** — Claude Code 2.1.316 does not read
  `sub/AGENTS.md` beside `sub/CLAUDE.md`, also when it reaches `sub/` lazily from the root, but
  `repo budget` counted it as `lazy`, along with its `@` imports. It is now `off`, its imports are
  not counted, and a warning names the `CLAUDE*` file that turns it off. It stays `lazy` when one
  of those files is a link to it or a loaded file `@`-imports it, or when the same file is also
  reached as an `AGENTS.md` in another directory. A `CLAUDE*` file in a parent or a deeper
  directory does not turn it off. A tree with a root `CLAUDE*` file, and a profile whose guidance
  names hold no `CLAUDE*` name (Codex, Copilot), get the same budget as before. The `claude-code`
  profile's `guidance.notes` state the measured rule.
