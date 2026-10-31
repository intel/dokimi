### Fixed

- **`repo scan` says why a scan is `incomplete`** — the text output used to print a missing
  `also_scan` file like any glob that matches nothing, and `--fail-on-fail` exited 1 with
  `fail 0` and no message. A scan that is incomplete now prints an `incomplete: ...` line after
  the warnings, with the warnings that make it so; `--fail-on-fail` prints them on stderr as
  `✗ scan incomplete: ...` when it exits 1 on such a scan; and `repo_readiness.v1` lists them in
  `incomplete_reasons` (`[]` when the scan is complete). A complete scan prints what it did
  before. The guidance-only manifest in `docs/reviewing.md` no longer lists
  `also_scan: ["CONTRIBUTING.md"]`, which made every scan of a repository without that file
  incomplete.
