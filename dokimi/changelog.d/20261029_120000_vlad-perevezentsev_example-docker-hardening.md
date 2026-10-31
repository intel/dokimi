<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Security

- **The `docker` backend of example execution drops every capability** — a
  fenced example ran as the image's root with docker's default capabilities,
  and a setuid binary in the image (`su` in `python:3.11-slim`) still worked.
  The container now starts with `--cap-drop ALL` and `--security-opt
  no-new-privileges`, as `repo run` containers do. The snippet's directory is
  mounted world-readable (0755, the snippet 0644), because a root without
  capabilities cannot enter the host user's private temp directory.
