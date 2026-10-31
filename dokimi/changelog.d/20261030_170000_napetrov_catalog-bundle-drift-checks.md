### Added

- **`catalog scan` checks each skill's bundle and description, and detects drifted mirrors** —
  a relative Markdown link in `SKILL.md` to a file that does not exist is a `warn`
  (`link-missing`), and says so when the file exists from the catalog root instead (a wrong
  base). A link or `../` code span that reaches a file outside the skill directory is a `warn`
  (`link-escapes-skill`, `path-escapes-skill`): it breaks when the skill is installed alone.
  A description with no "when to use" clause is a `warn` (`description-no-when`), as is one
  whose words overlap another skill's by at least half (`description-near-duplicate`). Two
  skills in different paths with the same directory name and different `SKILL.md` text are
  a `warn` on both (`mirror-drift`), naming the other copies. Links inside code fences, URLs,
  anchors, and code spans that name no file are not checked. Skills with
  `disable-model-invocation: true` are exempt from the description checks. Warnings never
  change the exit code; only `--strict` makes an `error` fail.
