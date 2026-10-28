### Fixed

- **`catalog trigger-probe` no longer reports an empty probe as a run** — a
  `--trigger-set` with no labelled query (a questions set without `expected_skill`,
  or an empty set) ran nothing, wrote `"answers": []`, printed `"summary": {}` and
  exited 0. It now exits 2 with `Error: --trigger-set has no labelled queries`
  before any run, and writes no `--out` file. When runs happened but Claude Code
  listed no skills in any of them (an older Claude Code, or every run failing before
  its init event), `not_listed` stayed `[]` without a word; a warning on stderr now
  says `not_listed` was not checked. The output JSON and the exit code of that case
  are unchanged.
