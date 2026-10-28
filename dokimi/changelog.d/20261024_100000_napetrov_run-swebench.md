### Added

- **`tasks run --swe-bench <rows.jsonl>`** runs SWE-bench rows (Lite,
  Verified) with the `docker-*` harnesses: each row runs in its prebuilt
  `swebench/sweb.eval.*` image, `docker-oracle` applies the gold patch, and the
  verifier runs SWE-bench's eval script and grades the log with the repo's
  SWE-bench parser (reward 1 only when every `FAIL_TO_PASS` and `PASS_TO_PASS`
  test passes). `tasks inspect` now names `docker-*` as the consumer of a
  SWE-bench file whose repos and versions all have a test spec.
  The solve runs offline and the verifier on a PyPI allowlist by default;
  `--all` skips the 17 rows in `swebench.KNOWN_BAD` known to fail with the
  gold patch in the prebuilt image (live-network tests, Harbor's known
  issues). Every row of a file is checked before anything is staged.
