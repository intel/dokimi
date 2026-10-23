### Changed

- **`novelty analyze --estimate-only` projects the bill it will actually run.** The atom
  count is the larger of one per two spans and one per 23 artifact tokens, capped as
  before; the new `expected_calls` counts the verify, shorten, carrier and classify calls
  and half the retention-oracle pairs that escalate to a model, and `by_role` splits it
  into extractor, answerer and judge. `expected_usd` prices those calls at list prices,
  with the extractor's output per call set per tier, and is `null` when a model has no
  price. The constants were fitted on 13 of 25 measured runs; on the other 12 (held out)
  calls landed within 13%, atoms within 11% and dollars within 33%, all within the 35%
  tolerance. Dollars ran high on 5 runs and low on 7 (worst -33%), so `expected_usd` is not a
  ceiling. `total_calls` and `total_calls_if_every_pair_escalates` keep their formula but
  follow the new atom count, so they rise wherever the span rule left the count under the
  cap and the token rule gives more (a 588-token skill at `--tier standard`: 129 -> 229).
