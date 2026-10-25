### Changed

- **Pipeline summary reads through `dokimi.records`** — the per-condition
  averages and the delta average that `EvaluationPipeline.run` prints and returns
  are now read with `dokimi.records` instead of the `with_docs`/`without_docs`
  keys of the evaluation dicts. The numbers are unchanged. A test now holds every
  other reader of those keys to a list of exceptions that only shrinks.
