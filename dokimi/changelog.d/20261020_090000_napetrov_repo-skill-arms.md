### Added

- **`repo run` skill arms** — `--arm guidance:none+skill:<path>` installs one
  skill under `.claude/skills/<name>/`, and `skill-catalog:<dir>` installs
  every skill in a directory. `result.json` records `skills`
  (`skills_used`, `skill_invoked`, `skill_selected_correct`) and the agent's
  cache read/write token counts. `repo tasks check --arm` rejects a skill
  path that does not exist, and a skill whose `SKILL.md` `name` differs from its
  directory name. The arm is named by the directory (`skill:lint`), so
  `repo compare` can select it.
- **`args_if_present` in harness profiles** — arguments appended only when a
  file exists in the workspace and the tool mode is `all`.

### Fixed

- **claude-code starts a repo's MCP servers** — the stock profile passed
  `--strict-mcp-config` without `--mcp-config`, so a repo that ships
  `.mcp.json` ran with no MCP tools. It now passes `--mcp-config .mcp.json`
  when that file is present and `--tools all` is used (`--tools none`/`read` do not
  limit MCP tools, so they start no MCP server).
