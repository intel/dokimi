### Fixed

- **A pinned source refuses a `--manifest` that leaves the tree** — `eval check`, `repo run` and `eval tasks generate --from replay|prs` on `<repo>@<sha>` with `--manifest ../outside.yaml` now exit 2 with `Error: path escapes the repository: ../outside.yaml`, as an unpinned directory does; they reported a missing manifest and exited 1. A path that stays inside the tree, such as `a/../b`, is read as `b`.
