<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Added

- **`catalog check --upstream` and `--upstream-manifest` add provenance findings** — each skill that matches an upstream version gets `upstream-stale` (note: the copy is not at the upstream head), `license-dropped-from-upstream` (warn: an edited copy drops the upstream's `license`) and `risk-inherited-from-upstream` (warn: the copy keeps unrestricted Bash, `pipe-to-shell` or `permission-bypass-flag` that its upstream version also has). Offline, from the upstreams you name; not combined with `--per-skill` or `--kind tasks`.
