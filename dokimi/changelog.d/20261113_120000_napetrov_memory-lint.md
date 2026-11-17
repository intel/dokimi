### Added

- **`dokimi memory lint` checks an agent's memory directory** — the first leaf of the `memory` root (#728).
  It reads a Claude Code auto-memory directory (`MEMORY.md`, topic files, `archive/`) and reports, with no
  model call: index links to missing files (`fail`), an index past the 200-line / 25KB load limit (`fail`),
  files the index never links, frontmatter that is missing, not YAML or lacks `name`/`description`/`type`,
  unknown types, duplicate names, dangling `[[wikilinks]]`, relative dates ("today", "вчера") with no date
  next to them, and empty list slots left in the index. Exit 1 on a `fail` (on a `warn` too with `--fail-on warn`);
  `--json` prints `cli_output.v1`.
