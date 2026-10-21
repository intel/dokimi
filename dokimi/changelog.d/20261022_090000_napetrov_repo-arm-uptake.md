### Added

- **repo run: uptake of what a `patch:` arm added** — `meta.json` `arm_patch`
  lists the files the patch `added` (guidance files left out) and the
  `mcp_servers` of a `.mcp.json` it wrote, and `result.json` gets an `uptake`
  object: the agent's commands naming an added file, reads of one, and calls of
  those servers' tools. `repo report` shows the cells that used the arm's
  additions per row; `repo compare` lists uptake per treatment and warns when a
  treatment's cells used none of it, since its deltas then measure the patch's
  text, not the tool (#41 D4).
