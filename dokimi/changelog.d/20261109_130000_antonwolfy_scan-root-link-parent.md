### Fixed

- **`repo scan` judges the directory a root link makes as that directory** — with a root link
  `owner -> src/pkg`, a stale `owner/gone` was `unresolved` (`tried owner/gone`) and a glob
  `owner/*.cfg` that nothing matches was `unresolved` (`... a build may create it`), so both passed
  `repo scan --fail-on-fail`, while beside a real `owner/` they are `fail`. The parent-directory
  check now follows the link, also through a link higher up: `owner/gone` is `fail` (`owner/ exists
  but owner/gone does not`), a glob is matched in `src/pkg` and named below the link (`owner/x*` is
  `ok`, `glob owner/x* -> e.g. owner/x`) or is `fail` (`owner/ exists but nothing in it matches
  *.cfg`), and a directory claim `owner/f/` naming the file `src/pkg/f` is `fail` (`owner/f is a
  file, not a directory`) where it read `ok`. A link that dangles or names a file still makes no
  directory, so a path below it keeps its verdict. A `.gitignore` rule naming the path below the
  link's target (`src/pkg/.gitignore` with `*.cfg`) still reads it as a build output (`unresolved`).
