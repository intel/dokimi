### Fixed

- **A skill-reach catalogue case whose name holds a lone surrogate no longer stops `build_fixture`** — a `\ud800` or `\udcff` escape in a case `name` raised `UnicodeEncodeError` when the case's `SKILL.md` was written. The file is written with the lone surrogate replaced by `?`, so it stays valid UTF-8 and the run goes on; the file of a name that encodes as UTF-8 is unchanged.
- **A terminal-bench task with a lone surrogate in its `task.yaml` no longer stops the Harbor export** — a `\udcff` or `\ud800` escape in the metadata or the instruction raised `UnicodeEncodeError` when `harbor_view` wrote `task.toml` or `instruction.md`. Both files are written with the lone surrogate replaced by `?`, so they stay valid UTF-8; the files of a task that encodes as UTF-8 are unchanged.
