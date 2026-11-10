### Fixed

- **`python` examples run in the `docker` backend of `example_pass_rate`** — the
  container was told to run the host's interpreter path (the `sys.executable` of the
  Python running dokimi), which the image does not have, so every `python` example failed
  with `Exit code 127: timeout: failed to run command …` and lowered the pass rate. The
  docker backend now runs the image's `python3`; the `subprocess` backend keeps the host
  interpreter. `bash` and `sh` examples are unchanged.

### Changed

- **`example_pass_rate`, and with it the document and summary `score` and the soft
  gate, rise for documents with `python` examples in the `docker` backend** — those
  examples ran for the first time with the fix above, so a document whose python
  examples pass scores higher for the same inputs than before the upgrade, and a soft
  gate that failed on them can now pass. Compare such scores with runs made after it,
  not with older baselines.
