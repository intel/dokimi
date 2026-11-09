### Fixed

- **`novelty routing --max-usd` counts an unpriced reply cut at the output ceiling as an
  abstention** — a call with no known price stops a capped run, and that stop came before the
  check for a cut reply, so the cut text was scored as a pick (a cut `{"pick": 1}` picked
  card 1). It is now an abstention with its `raw_reply` kept, as a priced
  cut reply already was, in a single-artifact run and in `--all-vs-all`, and the run still stops
  with the cap's message.
