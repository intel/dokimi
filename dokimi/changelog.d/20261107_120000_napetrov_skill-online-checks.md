### Added

- **`catalog check --online` reports skills that cannot be installed or reached** — an opt-in network pass
  over each skill's `SKILL.md`: a `pip`, `npm` or `npx` package that PyPI or the npm registry answers 404 for
  (`package-not-found`), and an `http(s)` link that answers 404 or 410, or whose host does not resolve
  (`link-dead`). Both are warnings, so the exit code does not change. Without the flag no connection is opened.
  A 403, 429, 5xx, timeout or other network error is not a finding. Not combined with `--per-skill`.
