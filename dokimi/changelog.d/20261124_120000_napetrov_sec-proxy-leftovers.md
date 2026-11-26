<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Security

- **Proxy values stay off the `docker exec` command line.** The oracle and verifier steps of the `docker-*` harnesses
  passed a public-network proxy as `-e https_proxy=<value>`, so a proxy URL with `user:password@` was visible to every
  user of the host in the process list. The step now reads proxy values from a 0600 `--env-file`, as the sandbox's agent
  exec does. Values with line breaks reach the step by name through the Docker CLI's environment.
  Invalid UTF-8 reports the variable name before creating an env-file because Docker changes invalid bytes in transit.
- **A proxy with credentials that a task Dockerfile declares with `ARG` is warned about.** The build passes the host's
  proxy by name, so a Dockerfile `ARG https_proxy` records the value in the image history (`docker history`). When the
  value has `user:password@`, the run log and stderr now say so with `[build] WARNING`. Remove the `ARG` from the
  Dockerfile to keep the credentials out of the image. The build still runs. The image build of `dokimi repo run`
  (the repo's `.agent-eval` Dockerfile) warns the same way, on stderr.

### Fixed

- **A proxy variable that is set but empty is no longer passed to `docker build`.** `--build-arg NAME` is now given only
  for the proxy variables with a non-empty value.
