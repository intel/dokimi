### Added

- **`tasks run --swe-bench <rows.jsonl>`** runs SWE-bench rows (Lite,
  Verified) with the `docker-*` harnesses: each row runs in its prebuilt
  `swebench/sweb.eval.*` image, `docker-oracle` applies the gold patch, and the
  verifier runs SWE-bench's eval script and grades the log with the repo's
  SWE-bench parser (reward 1 only when every `FAIL_TO_PASS` and `PASS_TO_PASS`
  test passes). `tasks inspect` now names `docker-*` as the consumer of a
  SWE-bench file whose repos all have a test spec.
  The rows run offline by default; `--all` skips the 9 rows in
  `swebench.KNOWN_BAD` whose gold patch fails in the prebuilt image. A file
  with a duplicate or path-like `instance_id` is refused before anything is
  staged.
