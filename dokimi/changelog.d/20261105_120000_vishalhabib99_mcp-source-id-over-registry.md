### Fixed

- **An `mcp:` source's `;id=` now wins over the registry in every command** —
  `answers`, `questions`, `arms run` and `benchmark run` used a registered product's `context7_id`
  and ignored the `;id=` given on the source, so
  the same doc source could query different libraries depending on the command.
  All of them now resolve in one order: `--context7-id`, `;id=`, `context7_id`, then
  the client. Runs that passed `;id=` for a registered product queried the
  registry's library until now; rerun them if you meant the `;id=` one.
