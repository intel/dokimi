<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Fixed

- **A cost under $0.00005 reads `<$0.0001`, not `$0.0000`, in the `arms run` and
  `report model-compare` reports** — the Cost (USD) column of the arms report (each cell's report
  under `arms matrix` too) and Total Cost under Resource Usage printed a real but tiny cost as
  `$0.0000`, as if the run were free. They now print it as the task-run report already does.
