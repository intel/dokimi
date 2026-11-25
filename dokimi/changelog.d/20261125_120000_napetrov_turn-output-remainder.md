<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Added

- **`events.jsonl` closes a Claude run's `turn` events against its result totals.** A new turn-less
  `usage_remainder` line holds the result's token totals minus the turns of the same scope, so the
  turns plus the remainder equal the totals `result.json` reports. The gap is the tokens the turns
  do not count: the calls that left no assistant record (a helper model's) and, for output, the streamed
  undercount. Additive: `repo_event.v2` is unchanged in version.

### Fixed

- **A Claude `turn` event carries its `output` count.** It was `null` on every Claude turn. It is now
  the call's `output_tokens` as its assistant records streamed them, one count per message id (the
  largest of its records). That count can sit below the billed output: the remainder's `output` is the
  gap. `docs/events-format.md` states the invariant that holds (input and cache) and the one that does not.
