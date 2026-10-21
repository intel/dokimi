### Changed

- **No `egress.jsonl` when the sidecar's log is unknown** — a container cell whose
  sidecar log could not be read at teardown no longer writes an empty
  `egress.jsonl`, which read like a clean run, and removes one an earlier run left
  in the same output directory. `metrics.egress.status` stays
  `unknown`; a missing file means unknown. `docs/coding-harnesses.md` also notes
  that `docker-probe` reads `egress.status: violated` by design.
