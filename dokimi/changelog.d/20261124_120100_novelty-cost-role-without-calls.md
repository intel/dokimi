<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Fixed

- **A novelty run stopped before a role's first call keeps its dollar total.** The `cost`
  block of `skill novelty analyze` (and of every other command that rolls up several role
  clients) had `usd: null` whenever one role made no call, so an `analyze` that `--max-usd`
  stopped in extraction, before any answer or grade, reported its spend as unknown although
  every call it made was priced. A role with no call now adds nothing; `usd` is still `null`
  when a paid call has no price, and that role's own `by_role` row keeps `usd: null` (#229).
