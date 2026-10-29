### Changed

- **`novelty routing` skips a non-`SKILL.md` competitor with a warning** — a
  `skill:` competitor that names a file other than `SKILL.md`, or a directory without one,
  is left out of the field and reported on stderr. It used to abort the whole run, so a
  shell glob over a skills directory (which also matches helper scripts) could not be
  passed. The artifact under test is still refused.
