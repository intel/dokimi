### Fixed

- **A drive-relative tool path stays as written in a converted trace** — when a `copilot-json` trace
  names a path such as `C:foo` (a drive letter, then no separator) and the run started in a
  subdirectory, `events.jsonl` recorded it joined to that directory (`sub/C:foo`), a file that does not
  exist. It now keeps `C:foo` unchanged. A rooted `\AGENTS.md` and a relative `a\b.txt` read as before
  (`/AGENTS.md`, `sub/a/b.txt`).
