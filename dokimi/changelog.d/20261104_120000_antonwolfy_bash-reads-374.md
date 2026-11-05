### Fixed

- **Shell reads the converters dropped where the shell could not have moved are now `read`
  events, and shell functions no longer keep a read at the wrong directory or of a command that
  did not run** — `cd $(printf sub; true) | cat; cat AGENTS.md` and its `&` and backtick forms
  (the substitution's own `;` no longer ends the piped or backgrounded part), `source
  .venv/bin/activate; cat AGENTS.md` (a virtualenv's activate script alone, also with a redirection
  such as `> /dev/null`, is assumed to run no `cd`), `case $x in a) echo case;; esac; cat AGENTS.md` (a `case` opens only as a command word),
  `time cat AGENTS.md`, `time -p cat AGENTS.md`, `time X=1 cat AGENTS.md`, `X=1 time cat
  AGENTS.md` and `command cat AGENTS.md` now read `AGENTS.md`. `f() { cd sub; }; f; cat AGENTS.md`
  read the root's `AGENTS.md`; a call of a function whose body names `cd`, `pushd`, `popd`,
  `eval`, `source` or `.` (these two as a command, not of an activate script: `git add .` names
  none) now loses the directory as an `eval` does; a function an earlier Claude
  Code Bash call defined is not carried to the next, which starts in a new shell. A definition whose `{` is on a later line (`f()`, a newline, `{ cd
  sub; }`) no longer runs its body's `cd`: it is only defined. A call of a function named like a
  printer (`cat() { echo hi; }; cat AGENTS.md`, `\cat AGENTS.md` and a body in parentheses,
  `cat() ( echo hi )` or `function cat ( echo hi )`, too) is no read, while `command
  cat` and `env cat`, which run the program, read the file; one named like a directory builtin (`cd() { ...; }`)
  loses the directory, while `command cd sub` and `builtin cd sub` run the builtin and are followed.
  A function named `command`, `env`, `builtin` or `time` (not the keyword) runs in place of that
  wrapper: `command() { echo hi; }; command cat AGENTS.md` and `command cd sub` read and move
  nothing. `time -x cat AGENTS.md` and `time --help cat AGENTS.md` are no read (the keyword takes
  only `-p` and `--`), and a failed `source 'my env/bin/activate' && cat AGENTS.md` is no read.
  `env command cat AGENTS.md` and `/usr/bin/env command cat AGENTS.md` are no read, and `env
  command -p cd sub` moves no shell: `env` runs a program named `command`, not the builtin. So `guidance_read` and
  `unknown_reads` can change for such traces.
