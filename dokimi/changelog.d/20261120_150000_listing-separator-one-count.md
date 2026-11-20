### Fixed

- **`repo budget` and the skill arm's `skill_listing` receipt now count a skill's listing entry the same way.** `repo budget` left out the ` - ` between `description` and `when_to_use` and `skill_listing` counted it even for an empty `when_to_use`. Both now count it only for a non-empty `when_to_use`, so `repo budget` charges 3 more characters per such skill.
