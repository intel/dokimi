<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Added

- **`execution.mem_mb: null`**: trusted host examples can omit the memory cap and keep the
  CPU cap and timeout. On macOS this is required for `backend: subprocess`, where the default
  `mem_mb: 512` cannot be applied.

### Changed

- **Host subprocess examples are not run when their limits cannot be applied.** On Windows
  any limit, and on macOS a memory limit, stops the run with an error naming the limit
  (`dokimi run` exits 2), as does a limit the kernel rejects when the snippet starts, so
  `example_pass_rate` is neither 0.0 nor 1.0 for snippets that never ran. On macOS set
  `mem_mb: null`; on Windows use the `docker` backend. A process limit above
  an inherited hard limit (for example `ulimit -v`) is lowered to it instead of failing the
  launch. The `docker` backend refuses the same way when `mem_mb` is missing or not positive.
- **The isolation probe reports `network: untested`, not `closed`**, when `bash` or `timeout`
  is missing, including inside task containers on images without them. Re-run the probe on
  such an image with the tools installed to get a `closed` result.
- **`ci_local.py` runs the gitleaks scan on Linux x64 only through the workflow download.**
  Other hosts use an installed `gitleaks` at the workflow's pinned version, with a notice, or
  skip the scan with the reason; `--offline` no longer skips the native scan.

### Fixed

- **Windows `process.run` closes the child's stdout and stderr pipes at EOF** and after an
  interrupted launch, instead of leaving them for garbage collection (`ResourceWarning`).
