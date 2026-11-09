### Documentation

- **The catalog loaders say that skill bodies load up front** — `catalog check --per-skill` with `--jsonl`,
  `review_skill_dir` and `read_skill_manifest` now state that every `SKILL.md` is read before the first row
  or entry, so memory still grows with the catalog. Loading one skill at a time is tracked in #434.
