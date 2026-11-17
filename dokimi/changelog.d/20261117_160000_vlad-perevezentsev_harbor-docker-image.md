<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Fixed

- **`docker-*` harnesses run a Harbor task on its prebuilt `[environment] docker_image`** — a task
  that sets one and ships no `environment/Dockerfile` (10 of the 17 uxlfoundation/skills tasks:
  every answer-quality task and `sycl-device-discovery`) failed its cell with `docker build`'s
  "open Dockerfile: no such file". As in Harbor 0.24, the image is used whenever it is set (also
  beside a Dockerfile), pulled only when the daemon lacks it, within `build_timeout_sec`, and the
  loose files of `environment/` are copied into the workdir after the start. The container runs
  in the task's `environment.workdir` (`docker run -w`), so `solve.sh` and `test.sh` find relative
  paths. A `docker_image` that is not a string (`docker_image = 5`) now fails the task's load, as
  in Harbor. `--network build=none` does not stop the pull, as it does not stop a
  build's `FROM`. `metrics.image` records the image a cell ran on: its id and repo digests for a
  prebuilt one. A task without `docker_image` builds as before. A task that sets `docker_image`
  beside `environment/Dockerfile` (as the old `task.toml` template in
  `contributing-terminal-bench-task.md` did, with a local `intel-hpc-bench/...` tag) now runs on
  that image and no longer builds the Dockerfile; `run.log` says so, and a tag no registry has
  errors the cell with `docker pull failed`. Remove `docker_image` to keep building the
  Dockerfile. See `docs/coding-harnesses.md`.
