<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Fixed

- **A `skills.yaml` or a `benchmark --registry` file with a value PyYAML cannot build (a date such as `2026-99-99`) names the file** — `catalog check` says `invalid skills.yaml at <path>: ...` and the catalog evaluator `invalid YAML at <path>: ...`, as for a YAML syntax error, where they gave PyYAML's bare message. `benchmark preflight`, `run` and `batch` read `--registry` as `catalog list|show --kind product` do: a file that is not UTF-8 or not YAML, or that has such a value, prints `Error: cannot read --registry <path>: ...` and exits 2 instead of a traceback, and a missing path or a directory prints `Error: --registry <path>: not a file` and exits 2. A missing file was read as an empty registry (also exit 2, the target not found or `No libraries to run.`), and a directory was a traceback.
