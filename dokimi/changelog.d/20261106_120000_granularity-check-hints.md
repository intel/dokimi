### Added

- **`catalog check` granularity hints** — after the skill rows, `catalog check` prints a
  free, static `# granularity` section: skill pairs that look like merge candidates
  (overlapping descriptions and trigger clauses, duplicated sections or code blocks) and
  skills that look like split candidates (two of: a long body, disjoint `when` clauses,
  unrelated section topics). It calls no model, is hints only, and never
  changes the exit code. The `--format json` report gains a `granularity` block with
  `hints_only: true` and no verdict. The paid follow-up command its `next:` line names is
  planned and not yet available.
