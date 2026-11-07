### Fixed

- **An `mcp:` source's `;id=` now wins over the registry in every command** —
  `answers`, `questions`, `arms run` and `benchmark run` used a registered product's `context7_id`
  and ignored the `;id=` given on the source, so
  the same doc source could query different libraries depending on the command.
  All of them now resolve in one order: `--context7-id`, `;id=`, `context7_id`, then
  the client. Runs that passed `;id=` for a registered product queried the
  registry's library until now; rerun them if you meant the `;id=` one.
- **`arms run` resolves the library id per doc arm** — with several doc arms,
  the first arm's id was used for all of them, so an `;id=` on any later arm was
  ignored and an `;id=` on the first arm leaked into the others. `agent:` doc arms
  and doc arms inside a `+` stack are resolved too, and each doc arm records its
  `library_id`. An `agent:` arm on a product with no id now stops with the same
  error as an `mcp:` arm, instead of querying the product name. An empty
  `--context7-id ""` now counts as unset, as in `answers` and `questions`,
  instead of querying the product name.
