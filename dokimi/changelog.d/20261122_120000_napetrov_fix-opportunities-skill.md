### Added

- **`dokimi-fix-opportunities` Agent Skill** (`data/skills/dokimi-fix-opportunities/`). Point a coding agent at it to work through `report opportunities --format json`: it takes the top three `act_now` items, applies each `execution_prompt`, re-checks eligible findings and compares before and after by `source_key`. A novelty re-run requires the user to request the paid model call. Load it as a `skill:` arm or copy it into your agent's skills folder.
