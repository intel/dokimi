### Added

- **Usage and cost for `novelty routing --model`** — a model run now writes a `cost` block into `routing.json` (calls, input and output tokens, cache read and write tokens, and `usd`, which is `null` when any call had no known price) and prints the same totals on stdout, so a routing measurement states what it cost. The cache token totals are also added to the per-role cost summary, so `novelty analyze` reports them in its `cost` block.
