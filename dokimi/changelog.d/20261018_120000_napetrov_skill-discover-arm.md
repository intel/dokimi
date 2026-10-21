### Added

- **Skill discovery arm (`docker-claude-skill-discover:<skill>`,
  `docker-claude-host-skill-discover:<skill>`)** — installs the skill exactly as
  `docker-claude-skill:<skill>` does (same plugin, same `Skill` tool, same launch), but the
  prompt does not open with the treatment notice, so the agent finds the skill by its
  description or not at all. Run it beside `docker-claude` and `docker-claude-skill:<skill>`
  to tell "the skill helps once loaded" from "the agent picks it up by itself". The default
  skill arm is unchanged.
- **Skill rows say which arm they are** — `docker-claude*-skill*` rows record
  `metrics.skill_notice` (`true` on the skill arm, `false` on the discovery arm) and
  `metrics.skill_listing` (`description_chars`, the listing's per-skill cap and whether the
  description was `truncated` in Claude Code's skill listing). A discovery cell whose
  description the listing cuts gets a warning in `run.log`.
- **Discovery gap in `tasks run` summaries and reports** — when a discovery arm runs next to
  its with-skill arm (same launch and skill directory, however the path is spelled), `summary.discovery_gaps` (`task_runs.v1`) holds both pass rates, the
  gap (with-skill − discovery, withheld when the arms ran or lost different cells, with
  `attempted_cells_match` and `errored_cells_match` saying which) and both skill-invocation
  rates, and the Markdown report gains a *Skill discovery* table.
