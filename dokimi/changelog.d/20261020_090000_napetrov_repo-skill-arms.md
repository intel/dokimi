### Added

- **`repo run` skill arms** — `--arm guidance:none+skill:<path>` installs one
  skill under `.claude/skills/<name>/`, and `skill-catalog:<dir>` installs
  every skill in a directory. `result.json` records `skills`
  (`skills_used`, `skill_invoked`, `skill_selected_correct`).
  `repo report --json` gives, per row, the mean of the token fields `result.json` already has
  (`input_tokens`, `output_tokens`, `cache_read_tokens`, `cache_write_tokens`).
  `repo tasks check --arm` rejects a skill
  path that does not exist, and a skill whose `SKILL.md` `name` differs from its
  directory name. The arm is named by the directory (`skill:lint`), so
  `repo compare` can select it. Resuming a batch with other skill content
  under the same arm name is refused.
- **`args_if_present` in harness profiles** — arguments appended only when a
  file exists in the workspace and the tool mode is `all`.

### Fixed

- **claude-code starts a repo's MCP servers** — the stock profile passed
  `--strict-mcp-config` without `--mcp-config`, so a repo that ships
  `.mcp.json` ran with no MCP tools. It now passes `--mcp-config .mcp.json`
  when that file is present and `--tools all` is used (`--tools none`/`read` do not
  limit MCP tools, so they start no MCP server).
  This shifts baselines: a repo that ships `.mcp.json` now starts its MCP
  servers under `--tools all`, so its results differ from earlier runs of the
  same repo and arm. Compare only batches run after this change.
- **A malformed `.mcp.json` is caught up front** — `repo validate` warns (and still succeeds) about one that is not a JSON object (or whose `mcpServers` is not) at a task base (a file that is not UTF-8 text included), and `repo run --tools all` exits before any cell, instead of every cell failing as a task failure when the CLI rejects the file; a `patch:` arm that writes one is a cell error.
- `check_arms` refuses, before any cell, a base that tracks `.claude` or `.claude/skills` as a symlink.
- A `guidance:none` arm can install a skill that the base ships under the guidance globs, because the arm drops those files first.
- A `SKILL.md` `name` that YAML reads as an impossible date is not a crash.
