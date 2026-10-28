### Changed

- **`docker-*` task containers are hardened like `repos` containers** — the
  task container starts with `--init`, `--cap-drop ALL`, `--security-opt no-new-privileges`
  and `--pids-limit 32768`, and gets back only `CHOWN`, `DAC_OVERRIDE` and `FOWNER`
  (the harness's own root steps on the agent's tree) and `SETUID` and `SETGID`
  (task scripts that drop privileges, such as apt's `_apt` sandbox). A setuid binary
  in a task image can no longer raise its privileges. Every other capability Docker
  grants by default is dropped, so a solve or verifier step that needs one now fails:
  killing another user's process (`KILL`), raw sockets (`NET_RAW`), `mknod`
  (`MKNOD`), `chroot` (`SYS_CHROOT`). Setting the setgid bit on a file whose group
  root is not in (`FSETID`) exits 0 and leaves the bit off. A port below 1024 still
  binds. Before, the container ran with Docker's default capabilities and no process
  limit. `docker-*` results from before this change may not be comparable for a task
  whose step needs a dropped capability: re-run it to compare.
