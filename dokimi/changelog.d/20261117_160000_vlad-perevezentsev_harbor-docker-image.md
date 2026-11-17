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
  loose files of `environment/` are copied into the workdir after the start (what creates `/app`
  in `python:3.12-slim`). `--network build=none` does not stop the pull, as it does not stop a
  build's `FROM`. `metrics.image` records the image a cell ran on: its id and repo digests for a
  prebuilt one. A task without `docker_image` builds as before. See `docs/coding-harnesses.md`.
