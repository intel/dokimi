### Changed

- **`novelty analyze --estimate-only` projects the bill it will actually run.** The atom
  count is the larger of one per two spans and one per 25 artifact tokens, capped as
  before; the new `expected_calls` counts the verify, shorten, carrier and classify calls
  and half the retention-oracle pairs that escalate to a model, and `by_role` splits it
  into extractor, answerer and judge. `expected_usd` prices those calls at list prices,
  and is `null` when a model has no price. Against five measured standard runs the calls
  landed within 20% of the counted total on all six, where the old span-only figure was
  37-69% under. `total_calls` and `total_calls_if_every_pair_escalates` are unchanged.
