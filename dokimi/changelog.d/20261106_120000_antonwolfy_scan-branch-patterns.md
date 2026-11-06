### Fixed

- **`repo scan` reads a branch name or pattern the repository configures as a branch, not a
  path** — guidance such as "The `no-commit-to-branch` hook fails on `master` and
  `maintenance/*`" left `maintenance/*` `unresolved` ("matches nothing in the tree"). A path or
  glob the tree does not hold, outside a root directory, is now `ok` when a `--branch` name or
  `--pattern` regex of the `no-commit-to-branch` hook in `.pre-commit-config.yaml`, or a
  `branches` / `branches-ignore` filter of `on.push` or `on.pull_request` in a workflow, names
  it, and the reason gives the source and its line (`branch pattern of no-commit-to-branch
  (.pre-commit-config.yaml:17)`).
