### Fixed

- **`repo scan --help` no longer names a stale mypy pin** — the `--typecheck`
  help and the repository evaluation contract pointed at `mypy==2.4.0` while
  `requirements-test.txt` tests mypy 2.4.1. Both now point at the pin in
  `requirements-test.txt` without repeating the version.
