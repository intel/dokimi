### Added

- **Per-phase network policy for `docker-*` runs** — build, solve and verify
  each get `none`, `allowlist` (egress sidecar) or `public`. Defaults: build
  public, solve and verify none. Set from task.toml (`[environment]`,
  `[agent]`, `[verifier]` `network_mode` / `allowed_hosts`, Harbor's
  `allow_internet`), or `dokimi tasks run --network PHASE=MODE[:hosts]`.
  The effective plan and each phase's source land in `metrics.network`; a
  skill's `metadata.network` is recorded as `metrics.network.skill`, not
  granted.

### Changed

- **A task whose `network_mode` is not `none`, `allowlist` or `public`, or that
  lists `allowed_hosts` without `allowlist`, now errors the run** instead of
  running offline.
