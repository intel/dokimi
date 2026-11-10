### Fixed

- **`python -m dokimi.schema_compat` exits 2 when git cannot read `--base`** — a base that is not a revision (`--base no-such-ref`) ended in an uncaught `GitError` traceback and exit 1, the status of a breaking schema edit, so the `schema_compat vs the PR base` step let a PR with the `schema-break-ok` label pass on a base it never read. It now prints `cannot diff against <base>: <git's error>` to stderr and exits 2, and the CI step fails on status 2 whatever the label; only status 1 is accepted by the label.
