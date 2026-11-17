### Fixed

- **A skill-reach catalogue case whose name holds a lone surrogate no longer stops `build_fixture`** — a `\ud800` or `\udcff` escape in a case `name` raised `UnicodeEncodeError` when the case's `SKILL.md` was written. The file is written with the lone surrogate replaced by `?`, so it stays valid UTF-8 and the run goes on; the file of a name that encodes as UTF-8 is unchanged.
