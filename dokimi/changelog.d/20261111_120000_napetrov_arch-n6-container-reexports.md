### Removed

- **`dokimi.repos.container` no longer re-exports `PIDS_LIMIT`, `PROXY_BUILD_ARGS` and `STOP_GRACE_S`** — they
  were never used from there and stay defined in `dokimi.docker_cmd`. Code that imports them from
  `dokimi.repos.container` must import them from `dokimi.docker_cmd` instead. Runtime behaviour is unchanged.
