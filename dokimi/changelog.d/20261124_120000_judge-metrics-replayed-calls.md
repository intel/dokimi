<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Fixed

- **`judge_metrics` no longer bills a judge call replayed from the response cache.** In the
  `eval` and `arms run` artifacts, a call `DOKIMI_RESPONSE_CACHE` replays now counts in the new
  `replayed_calls` and adds no tokens and no dollars, as `novelty`'s cost block already does: a
  rerun from the cache used to report the original run's tokens and `cost_usd` as spent again.
  A judge call that returned no usage now counts in `calls` as a call with no price, so
  `cost_known_calls` falls short of `calls` instead of the call going unrecorded (#229).
