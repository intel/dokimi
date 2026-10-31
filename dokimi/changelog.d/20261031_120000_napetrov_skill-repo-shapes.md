### Added

- **Skill-repo shape tests** — `tests/test_skill_repo_shapes.py` runs `catalog scan`
  and `novelty routing --catalog` over thirteen invented repo layouts (byte-identical
  and drifted mirrors, same-name skills, `disable-model-invocation`, `template/`,
  vendored directories, a zero-skill repo, `plugins/*/skills` nesting, an over-long
  description, a name that is not the directory name, links that leave the skill
  directory, a skill with no headings). Each shape must not crash, and the bugs
  still open on `main` are strict expected failures until their fixes land.
