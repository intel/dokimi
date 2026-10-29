### Added

- **`eval redteam` checks that grading blocks reject wrong answers** — grades a planted wrong, an empty, a hedged and a keyword-stuffed answer against each question's `grading` block, and a canary right answer built from its one literal check. A wrong answer the grader accepts, or a canary it rejects, is a violation: the command prints pass rates, exits 1 and, with `-o`, writes a `redteam.v1` artifact. Judge checks are unscored unless `--judge` is given, so the default run makes no model call.
