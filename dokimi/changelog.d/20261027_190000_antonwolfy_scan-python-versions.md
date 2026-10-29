### Added

- **`repo scan` checks the Python versions a guidance line gives for a workflow against its
  matrices** — a table row such as `` | conda tests, py3.12-3.14t | `conda-package.yml` | `` went
  stale when the workflow's matrix moved on, and every path and name on it still resolved. On a
  line that names a workflow under `.github/workflows/`, a dashed range (`py3.12-3.14t`) or a list
  of two or more versions (`Python 3.12, 3.13 and 3.14`) is now a `python_versions` claim, `fail`
  when the workflow, and the local workflows it calls, run other versions: the values of matrix
  keys that name Python, less what `exclude` takes out of every combination, and the
  `python-version` a setup step pins, or that a caller passes to a called workflow's input. A
  `3.14t` value or a `cp314t` tag is a free-threaded build, and a pre-release (`3.14.0-rc.1`) runs
  its minor version. A computed matrix, `include`, `exclude` or step version, a value such as
  `3.x` or `>=3.12`, a `python-version-file` with no `python-version`, a call to another
  repository's workflow, or no Python version make the claim `unresolved`, and so does a range
  that holds for the matrices and not with a setup step's pin, or the other way round (a lint job
  on another Python).
