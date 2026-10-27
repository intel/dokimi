<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Fixed

- Reject unsupported host process limits before launching commands, and prevent
  execution when applying a resource limit fails. Native isolation probes report
  incomplete raw-network coverage as untested when Bash or timeout is missing.
- Keep local CI from executing its pinned Linux x64 gitleaks binary on other hosts;
  use an installed native binary at the exact workflow version or report the job
  as unsupported.
- Allow trusted host examples to explicitly omit the memory cap with `mem_mb: null`, retaining CPU and timeout limits; reject absent or nonpositive Docker memory caps.
