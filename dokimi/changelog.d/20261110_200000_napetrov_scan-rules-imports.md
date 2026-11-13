### Changed

- **`repo scan` without a manifest also checks `.claude/rules` and `@` imports** — besides the
  default guidance globs, a scan with no `repo-eval.yaml` and no `--manifest-file` checks every
  `.claude/rules/**/*.md` file and every Markdown file Claude Code loads with them: the files a
  CLAUDE.md `@` import chain reaches (what `repo budget` lists as `rule` and `import` for the
  `claude-code` profile), and a committed `CLAUDE.local.md` that nothing imports, which Claude Code loads
  with the project's guidance (a gitignored one is not in the tree). A stale path in a rule or an imported
  file reaches the agent as surely as one in CLAUDE.md, and was not checked before. They are named by path,
  so a repository with no rules gets no new warning; a non-Markdown import (`@package.json`) is not read as
  guidance. A written manifest scans what it names, as before. Two gates can move: a default scan's, since a
  stale claim in a rule now fails it, and a rule that links to nothing or out of the tree, or a
  rule the tree cannot list (a linked `.claude/rules`, a root `.claude` link, a submodule there) makes the scan
  `incomplete`; and any scan's, since a rule's frontmatter is no
  longer checked (Claude Code does not load it), so a stale path there no longer fails it.
