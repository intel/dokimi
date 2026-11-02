### Added

- **`repo run` arms `guidance:format=xml` and `guidance:as-skill`** — the guidance files of the base, rendered as XML tags (each Markdown heading becomes a `<section>` element, checked to restore to the original file exactly), or moved into one skill directory (`.claude/skills/<repo>-guidance/SKILL.md`). Both compare the same guidance content in another shape under the same model; `guidance:format=md` is refused as a duplicate of `guidance:raw`, and POML is not offered yet.
