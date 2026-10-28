### Changed

- **`novelty routing` leaves `disable-model-invocation` skills out of the field** — no
  harness lists such a skill, so the model cannot select it, and a card for it scored a
  choice that does not exist (`ultracave` and `megacave` in the caveman repo). They are
  left out of a `--catalog` and of `--competitors`, each with a warning on stderr, unless
  `--include-disabled` is passed. A disabled artifact is still routed, with a warning that
  its rate is not a harness rate.
