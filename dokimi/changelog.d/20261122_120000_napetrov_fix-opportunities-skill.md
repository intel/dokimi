### Added

- **`dokimi-fix-opportunities` Agent Skill** (`data/skills/dokimi-fix-opportunities/`). Point a coding agent at it to work through `report opportunities --format json`: it takes the top three `act_now` items, applies each `execution_prompt`, re-runs the check that produced the finding and reports before/after by `source_key` (a key still present is not fixed). Load it as a `skill:` arm or copy it into your agent's skills folder.
