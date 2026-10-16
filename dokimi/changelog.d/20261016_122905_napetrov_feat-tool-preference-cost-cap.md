### Added

- **Tool-preference cost cap (`--max-cost-usd`)** — stop starting new model calls once the sum of `cost_usd` from completed calls reaches the cap. The run exits 2 when stopped by cost or when a call has no price; the partial artifact records `max_cost_usd`, `skipped` calls, and the count of skipped items (`n_skipped`). Calls already in flight finish, so the spend can overshoot by up to `--concurrency − 1` calls. A call that fails but was billed (and carries usage) counts toward the cap. The last group reached may be partial.
