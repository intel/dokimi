### Added

- **`eval headroom` no longer counts a cached repeat as a fresh rep** — `arms run` now stores
  `replayed` with each single-turn answer: `true` when `DOKIMI_RESPONSE_CACHE` served it instead
  of a model call. `eval headroom` leaves out a replayed answer whose text a fresh answer of
  another input file has for the same item and arm, so a repeat run served from the cache no longer makes an item
  non-provisional or moves its means; the count left out is noted under the table and written as
  `summary.replayed_repeats` in the `headroom.v1` file. Replays of one answer that no fresh input
  gave count once, at the mean of their scores, and arms files written before the flag are read
  as before.
