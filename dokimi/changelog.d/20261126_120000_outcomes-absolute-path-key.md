### Fixed

- **`trace consistency --outcomes` keys written as absolute paths now match relatively named traces.** Before, such a key matched nothing and every run was left unjudged. Two keys that spell one trace with different verdicts are refused. `docs/run-consistency.md` now lists the verdict strings the command prints and names the test action in its example contract by kind, so `command -v pytest` no longer counts as a test run.
