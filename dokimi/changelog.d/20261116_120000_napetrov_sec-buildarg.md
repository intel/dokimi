<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Security

- **`docker-*` task images are built with the host proxy by name, not by value.** The image build
  used to pass `--build-arg https_proxy=<value>` (and the other proxy variables) on the docker
  command line, so a proxy URL with `user:password@` was visible to every user of the host in the
  process list. The build now passes `--build-arg <NAME>`, and the docker CLI reads the value from
  its own environment, so the command line and `ps` no longer show it. A task Dockerfile that
  declares `ARG https_proxy` still records the value in the image history, as before: remove the
  `ARG` from the Dockerfile to keep it out of the history.
