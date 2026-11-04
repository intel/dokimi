### Fixed

- **Shell reads the converters dropped where the shell could not have moved are now `read`
  events, and a call of a shell function that runs a `cd` no longer keeps a read at the wrong
  directory** — `cd $(printf sub; true) | cat; cat AGENTS.md` and its `&` and backtick forms
  (the substitution's own `;` no longer ends the piped or backgrounded part), `source
  .venv/bin/activate; cat AGENTS.md` (a virtualenv's activate script alone is assumed to run no
  `cd`), `case $x in a) echo case;; esac; cat AGENTS.md` (a `case` opens only as a command word),
  and `time cat AGENTS.md`, `time -p cat AGENTS.md`, `time X=1 cat AGENTS.md`, `X=1 time cat
  AGENTS.md` now read `AGENTS.md`. `f() { cd sub; }; f; cat AGENTS.md` read the root's
  `AGENTS.md`; a call of a function whose body names `cd`, `pushd`, `popd`, `eval`, `source` or
  `.` now loses the directory as an `eval` does, also when an earlier Claude Code Bash call
  defined it. So `guidance_read` and `unknown_reads` can change for such traces.
