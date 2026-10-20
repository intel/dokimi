### Added

- **`repo run` skill arms** — `--arm guidance:none+skill:<path>` installs one
  skill under `.claude/skills/<name>/`, and `skill-catalog:<dir>` installs
  every skill in a directory. `result.json` records `skills`
  (`skills_used`, `skill_invoked`, `skill_selected_correct`) and the agent's
  cache read/creation token counts. `repo tasks check --arm` rejects a skill
  path that does not exist.
- **`args_if_present` in harness profiles** — arguments appended only when a
  file exists in the workspace.

### Fixed

- **claude-code starts a repo's MCP servers** — the stock profile passed
  `--strict-mcp-config` without `--mcp-config`, so a repo that ships
  `.mcp.json` ran with no MCP tools. It now passes `--mcp-config .mcp.json`
  when that file is present.
