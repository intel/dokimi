### Fixed

- **`repo scan` no longer fails or leaves unresolved a GitHub Action named without `@ref` that the
  workflows use** — guidance such as "Every `actions/checkout` sets `persist-credentials: false`"
  had `actions/checkout` checked as a path: `unresolved`, or `fail` in a `.github/AGENTS.md` once
  the repository holds local actions under `.github/actions/`, which failed `--fail-on-fail`. An
  `owner/name` (or `owner/repo/path`) that the tree does not hold, outside a submodule, is now
  `ok` when a job or step of a `.yml`/`.yaml` workflow directly in `.github/workflows/` (not a
  nested one), or of a local composite action under `.github/`, uses it as
  `uses: <name>@<ref>`, and the reason names that file (`used by .github/workflows/ci.yml`). A
  name whose directory the tree holds at the repository root (`tools/gone` beside `tools/`) is
  still judged as a path, so a stale path still fails. A name only in a `run:` script does not
  count, and an Action no step uses gets the verdict it got before. A workflow PyYAML cannot
  build (a date that does not exist, `!!bool maybe`, `!!timestamp foo`, deep nesting) is skipped
  here and leaves a Python range naming it `unresolved`, instead of aborting the scan.
