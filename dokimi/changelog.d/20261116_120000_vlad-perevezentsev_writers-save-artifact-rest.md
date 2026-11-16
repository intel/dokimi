### Fixed

- **`docs check`, `dashboard generate` and the baselines manifest are saved as artifacts** —
  the `docs check` snapshot (`--out`, `baselines/current.json` by default), `dashboard.json`
  and `baselines/eval/manifest.json` were plain JSON dumps: no `schema_version`, not atomic,
  and `runs list` did not see them. They are saved through `save_artifact` now, as the new
  kinds `docs_run` and `dashboard` (with the run envelope) and `baseline_manifest` (an index
  every `baseline save` rewrites, so no envelope). A payload its schema refuses is not
  written: it is kept at `<name>.rejected` and the command exits 1 with
  `Error: <kind> artifact rejected: …`. `baseline save` copies the eval file byte for byte
  and atomically, so the baseline keeps the eval's own `schema_version` and `run_id`.
  Snapshots and manifests written before still read: `docs compare` and `baseline
  list|compare` take them as they are.
