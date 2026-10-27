### Changed

- **`docker-*` task containers are hardened like `repos` containers** — the
  task container starts with `--cap-drop ALL`, `--security-opt no-new-privileges`
  and `--pids-limit 32768`, and gets back only `CHOWN`, `DAC_OVERRIDE`, `FOWNER`,
  `SETUID` and `SETGID`, which the harness's own root steps (the agent's tree,
  `apt`, `setpriv`) need. A setuid binary in a task image can no longer raise its
  privileges. A task whose solve or verifier needs another capability now fails at
  that step: killing another user's process (`KILL`), binding a port below 1024 on a
  host where `net.ipv4.ip_unprivileged_port_start` is 1024 (`NET_BIND_SERVICE`), or
  raw sockets (`NET_RAW`). Before, the container ran with Docker's default
  capabilities and no process limit.
