### Fixed

- **repo run: uptake of a root-level file** — a bare `lint` at the start of a
  command no longer counts as a run of the arm's `lint` file, since a bare name
  resolves through `PATH`; it counts as `./lint`, under the workspace path, or
  after an interpreter.
