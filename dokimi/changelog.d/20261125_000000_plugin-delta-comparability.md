### Fixed

- **Plugin comparisons.** `arms plugin-delta` and matrix rollups refuse pairs with different question text, profile prompts, judge references or judge settings. They also refuse pairs when only one run records question text or a non-null judge setting. Reports warn about question text missing from both runs, setting fields omitted by either run, and null judge prompt digests.
