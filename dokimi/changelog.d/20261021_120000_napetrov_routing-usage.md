### Added

- **Usage and cost for `novelty routing --model`** — a model run now writes a `cost` block into `routing.json` (calls, input and output tokens, cache read and write tokens, and `usd`, which is `null` when any call this run paid for had no known price) and prints the same totals on stdout, so a routing measurement states what it cost. Calls replayed from the response cache are counted in `replayed_calls` and left out of the token and `usd` totals (`llm_calls` still counts them), and `usd` is also `null` when a call returned no usage.

### Changed

- **`novelty analyze` cost totals are what this run spent** — the per-role cost summary now leaves calls replayed from the response cache out of `input_tokens`, `output_tokens` and `usd` (they were summed from the stored records before) and counts them in a new `replayed_calls`; `llm_calls` still counts them. `usd` is now `null` when a call returned no usage, where it was the sum of the priced calls. The block also gains `cache_read_tokens` and `cache_write_tokens`, and the Markdown report's cost line names the replayed calls when there are any.
