### Fixed

- **A skill the OS refuses to read is an error row, not a crash** — `catalog scan` and the other catalog reviews
  no longer stop on an `OSError` (`ENAMETOOLONG`, say) raised while checking one skill. A `SKILL.md` that
  cannot be read after it loaded is reported as an `unreadable` error for that skill; a skill that did not load
  and whose directory cannot be stat-ed gets no review. The other skills are still reviewed.
