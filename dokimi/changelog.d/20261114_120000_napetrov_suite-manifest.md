<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Added

- **Suite manifests for tasks whose base is in another repository** — `suite_manifest.v1` lists eval-tree tasks whose base is a commit of a scaffold repository, referenced as `<url>@<sha>`. `repo run --suite <path>` resolves each base there; a repository that cannot be opened leaves its cells unsupported, not failed. Floating refs need `--allow-floating-suite-revs`. `repo tasks check` exits 1 (incomplete) when every selected task is such a gap, since nothing was checked. See `docs/repo-evaluation-contract.md` §2.1.
