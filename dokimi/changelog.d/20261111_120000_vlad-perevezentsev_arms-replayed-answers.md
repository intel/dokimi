### Added

- **`arms run` records which answers the response cache served** — each single-turn answer
  the model gave now carries `replayed`: `true` when `DOKIMI_RESPONSE_CACHE` served it instead
  of a model call. `eval headroom` writes `summary.replayed_repeats` in the `headroom.v1` file:
  the scores of replayed answers that added no rep.

### Changed

- **`eval headroom` no longer counts a cached repeat as a fresh rep** — a replayed answer whose
  text a fresh answer of another input file has for the same item and arm is left out of the
  reps and the means, so a repeat run served from the cache no longer makes an item
  non-provisional or moves its means. Replays of one answer that no fresh input gave count
  once, at the mean of their scores. A note under the table gives the count; arms files written
  before the flag are read as before.
