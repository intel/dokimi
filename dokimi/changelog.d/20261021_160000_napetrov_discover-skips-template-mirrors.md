### Fixed

- **Skill discovery skips templates and counts a mirror once** — `discover_skills` (behind the
  `skill-catalog:` arm, `catalog trigger-set`, `catalog scan` without a `skills.yaml`, and
  `novelty routing --catalog`) used to load every `SKILL.md` under the directory. A `SKILL.md`
  under a `template/` or `templates/` directory is a scaffold, not a skill, and is now skipped.
  A `SKILL.md` byte-identical to another under the directory (a plugin's copy of the `skills/`
  tree) is offered once: the copy nearest the root stands for it. Same-named skills whose text
  differs are still both offered. `discover_skills(root, notes=[])` lists each one it leaves out.
