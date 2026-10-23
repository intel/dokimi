### Fixed

- **A response-cache entry of the wrong shape is a miss, not a crash** —
  `load_response` already treated unparseable JSON as a miss, but an entry that
  parsed into a list, a number, a numeric text or a non-object `usage` raised
  `TypeError`/`AttributeError` (or replayed a non-string answer) and ended the
  run. A `null` text, which a reply without content is stored as, still
  replays. A wrong-shape entry now reads as a miss and the call is made again.
