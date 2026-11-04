### Fixed

- **Shell reads the converters dropped where the shell could not have moved are now `read`
  events, and shell functions no longer keep a read at the wrong directory or of a command that
  did not run** — `cd $(printf sub; true) | cat; cat AGENTS.md` and its `&` and backtick forms
  (the substitution's own `;` no longer ends the piped or backgrounded part), `source
  .venv/bin/activate; cat AGENTS.md` (a virtualenv's activate script alone is assumed to run no
  `cd`), `case $x in a) echo case;; esac; cat AGENTS.md` (a `case` opens only as a command word),
  `time cat AGENTS.md`, `time -p cat AGENTS.md`, `time X=1 cat AGENTS.md`, `X=1 time cat
  AGENTS.md` and `command cat AGENTS.md` now read `AGENTS.md`. `f() { cd sub; }; f; cat AGENTS.md`
  read the root's `AGENTS.md`; a call of a function whose body names `cd`, `pushd`, `popd`,
  `eval`, `source` or `.` now loses the directory as an `eval` does, also when an earlier Claude
  Code Bash call defined it. A definition whose `{` is on a later line (`f()`, a newline, `{ cd
  sub; }`) no longer runs its body's `cd`: it is only defined. A call of a function named like a
  printer (`cat() { echo hi; }; cat AGENTS.md`, `\cat AGENTS.md` too) is no read, while `command
  cat` and `env cat`, which run the program, read the file; one named like a directory builtin (`cd() { ...; }`)
  loses the directory. So `guidance_read` and `unknown_reads` can change for such traces.
