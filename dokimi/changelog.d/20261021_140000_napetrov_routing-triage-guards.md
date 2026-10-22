### Fixed

- **`novelty routing --queries-from-headings`** no longer reads a `##` line inside a
  fenced code block as a request, so a shell comment does not become a query.
- **`novelty routing` with no usable query** says why and suggests `--queries`, and
  writes the result with `"status": "no_queries"` (the reason is in `reason`), where it
  used to exit with a bare "no usable queries". A successful result carries `"status": "ok"`.
- **Every `novelty routing` rate line** prints its `n_queries`, and a self-derived rate
  carries `triage: self-derived` on the same line. A run that scores fewer than five
  queries prints a warning, since one query moves a rate by 20 points.

### Changed

- **`novelty routing` exits 1 when no query is scored** — a run whose queries are non-empty
  but all filtered out (no content word left) now exits non-zero with `"status": "no_queries"`
  and a `reason`, where it used to exit 0 with null rates. A script that read the null rates
  as a result must check the exit code or `status` instead.
