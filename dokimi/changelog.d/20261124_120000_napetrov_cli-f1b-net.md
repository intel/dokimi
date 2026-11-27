<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Changed

- **Network, timeout and cache flags have one spelling each (part of #94, F1b).** The phase timeouts are one
  `--timeout [PHASE=]SEC`: `repo run` and `eval check` `--build-timeout` is `--timeout build=SEC`, `--grade-timeout`
  is `--timeout verify=SEC`, and a bare SEC is the agent run's (`repo run` only). `tasks run --network` takes
  `[PHASE=]MODE[:hosts]`. Bare `none` or `public` sets every phase. An allowlist must specify `solve`,
  `verify` or both, for example `--network solve,verify=allowlist:pypi.org`. `--cache NAME=DIR` is the one cache spelling in the
  help text. The old timeout spellings exit 2 and print the new command. `--agent-network`, `--build-network` and
  `--cache-root` keep their names: their values mean something other than the network policy or `--cache`.
