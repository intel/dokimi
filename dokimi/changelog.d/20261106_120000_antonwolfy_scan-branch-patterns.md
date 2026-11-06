### Fixed

- **`repo scan` reads a branch name or pattern the repository configures as a branch, not a
  path** — guidance such as "The `no-commit-to-branch` hook fails on `master` and
  `maintenance/*`" left `maintenance/*` `unresolved` ("matches nothing in the tree"). A path or
  glob that would be `unresolved`, outside a root directory, is now `ok` when a `--branch` name or
  `--pattern` regex of the `no-commit-to-branch` hook in `.pre-commit-config.yaml`, or a
  `branches` / `branches-ignore` filter of `on.push`, `on.pull_request`,
  `on.pull_request_target`, `on.merge_group` or `on.workflow_run` in a workflow, names
  it, and the reason gives the source and its line (`branch pattern of no-commit-to-branch
  (.pre-commit-config.yaml:17)`). A claim ending in a file extension the tree uses
  (`release/CHANGELOG.md`) matches only a name or pattern equal to it. A claim left
  `unresolved` names each of those files the scan did not read (`<file> was not read (<why>)`).
