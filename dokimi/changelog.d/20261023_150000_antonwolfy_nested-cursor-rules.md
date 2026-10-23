### Fixed

- **`repo inspect` lists a `.cursor/rules/*.mdc` below the root** — a rule
  file such as `web/.cursor/rules/style.mdc` was not listed in
  `instructions`. It is now a record with `directory` `web`, and with scope
  and activation `unknown`: Cursor's rules docs cover only the root
  `.cursor/rules` (and nested `AGENTS.md`), so whether Cursor reads it is not
  known from the docs. Its note says so.
