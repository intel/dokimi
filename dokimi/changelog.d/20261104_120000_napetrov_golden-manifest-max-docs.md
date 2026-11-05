### Changed

- **`golden_manifest.max_docs` is optional** — the spec schema no longer
  requires it, and the shipped `benchmarks/spec.v1.yaml` drops it: an absolute
  doc-count cap had to be raised with every new doc and failed main when two
  PRs each added one. A spec that sets it is still enforced; `min_docs` stays
  required.
