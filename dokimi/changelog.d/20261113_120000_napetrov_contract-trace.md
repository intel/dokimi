### Added

- **New error paths and tests trace to the contract** — `REVIEW.md` and `AGENTS.md` ask that a new `raise`,
  `sys.exit` or refusal names the doc, contract or issue behind it, that a test of new behaviour cites the doc
  line it checks, and that an unclear requirement goes in the PR template's new "Open questions" section.
  `python scripts/list_new_error_paths.py --base origin/main` lists the added sites for the reviewer; it always
  exits 0 (2 when git cannot diff).
