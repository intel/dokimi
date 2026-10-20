### Added

- **More L0 Bazel and CMake claims** (`repo scan`, ported from oneDAL's own
  checker, #41 step D6): a BUILD snippet fenced as `python` is checked as
  Starlark when it loads a label or calls a Bazel-only rule (`cc_library`,
  `http_archive`); the files of a top-level `exports_files([...])` count as
  target names (`exports_files(glob(...))` makes a missing name `unresolved`); each `load()` symbol must be a public top-level name of
  the in-tree `.bzl` it names (`starlark_load`); a label of an external
  repository is checked against that repository's BUILD file when the tree
  holds one (`<repo>.BUILD`, `BUILD.<repo>`, `<repo>.tpl.BUILD`, named by a
  `.bzl`, `MODULE.bazel` or `WORKSPACE`, and not a `bazel_dep`),
  and `@<module>//` alone is `ok`;
  `cmake -S <dir>` must name a directory with a `CMakeLists.txt`
  (`cmake_src`; `fail` only after a literal `cd`).

### Fixed

- **`<placeholder>` words in commands** — `bazel test //pkg/<algo>:tests`,
  `make <target>` and `pre-commit run <hook>` were split at `<` and `>` as
  redirections, so a cut label (`//pkg/`) was `unresolved` and `>` was looked
  up as a make target or hook id. A `<placeholder>` is now a word to fill in,
  like `$VAR`, and is not looked up.
