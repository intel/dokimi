### Changed

- **A scan with unread build inputs is `incomplete`** — an unreadable `.bzl`, BUILD,
  `.bazelrc` (or a file it imports), `MODULE.bazel` or `WORKSPACE` file (binary, over 2 MB, dangling link) now adds a
  `build file not scanned: <path>: <why>` warning to the `repo_readiness.v1` payload and sets
  `incomplete: true`, as an unreadable guidance file already did. Before, only the claims that
  needed the file were `unresolved`, and the scan itself looked complete.
