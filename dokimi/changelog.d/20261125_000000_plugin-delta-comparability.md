### Fixed

- **Plugin comparisons.** `arms plugin-delta` and matrix rollups refuse pairs with different question text, profile prompts, judge references, judge settings, answer budgets or sampling settings (`run_config`), or a seed pinned in only one run. They also refuse pairs when only one run records question text or a non-null judge setting. Reports warn about question text missing from both runs, setting fields omitted by either run, null judge prompt digests, and `run_config` recorded by only one run.
