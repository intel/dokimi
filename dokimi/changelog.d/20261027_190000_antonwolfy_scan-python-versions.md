### Added

- **`repo scan` checks the Python versions a guidance line gives for a workflow against its
  matrices** — a table row such as `` | conda tests, py3.12-3.14t | `conda-package.yml` | `` went
  stale when the workflow's matrix moved on, and every path and name on it still resolved. On a
  line that names a workflow under `.github/workflows/`, a dashed range (`py3.12-3.14t`) or a list
  of two or more versions (`Python 3.12, 3.13 and 3.14`) is now a `python_versions` claim, `fail`
  when the matrices of the workflow, and of the local workflows it calls, run other versions. A
  `3.14t` value or a `cp314t` tag is a free-threaded build. A computed matrix, a call to another
  repository's workflow, or no Python version in the matrices make the claim `unresolved`.
