### Added

- **`eval skill-coverage`** — lists the checkable requirements of a SKILL.md (triggers,
  out of scope, must hold, success criteria, failure modes) and names the eval checks
  that would fail on each one. Requirements no check names are printed as candidate
  checks to write. The map is LLM-estimated and labelled as such; no score or gate reads
  it. `--out` writes a `skill_coverage.v1` artifact. See `docs/evaluating-treatments.md`.
