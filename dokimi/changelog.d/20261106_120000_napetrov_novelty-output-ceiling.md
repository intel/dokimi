### Changed

- **Novelty calls ask for 16384 output tokens when `--max-tokens` is not given** — the
  4096 default that litellm sends to Bedrock-hosted Claude cut long extraction and
  judge replies. An explicit `--max-tokens` is sent as given.

### Fixed

- **A reply cut at the output ceiling is an error, not a silent bad parse** — a reply with
  `finish_reason` `length` or `max_tokens` now fails the call with "reply cut at the output
  ceiling" (its cost is still recorded). Extraction splits the batch and re-asks, as it does
  for unparseable replies, and a cached cut reply replays as the same error.
