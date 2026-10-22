### Changed

- **`novelty routing --catalog <dir>` reads the catalog through its `skills.yaml`** — when the
  catalog root has a `skills.yaml`, that manifest decides which skills are competitors, as it
  does for `catalog scan`, so a `SKILL.md` it does not list is no longer a sibling. Without a
  manifest, every `SKILL.md` under the directory counts as before. A declared skill that cannot
  be loaded is skipped with a warning on stderr instead of making the whole field unreadable.
  Two catalog skills with the same name and different content are refused, naming both paths,
  rather than the run failing on a duplicate competitor name.
