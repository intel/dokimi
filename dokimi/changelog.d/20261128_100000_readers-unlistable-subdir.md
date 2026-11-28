<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Fixed

- **Six readers of a user's tree refuse a directory they cannot list, as input errors (exit 2), instead of reading a smaller tree** — they used `Path.rglob` or `os.walk` with no `onerror`, which skip a directory they cannot list (mode `0o111`) without an error. Now `docs check`'s discovery of Markdown under `docs/` (when its spec has no `golden_manifest`) (a `.git`, `node_modules` or other vendored directory is still not listed), the exploration task files under `--tasks-root`, the test files `eval skill-coverage` reads (its checks and the `--out` guard now read the same files, so an `--out` can no longer name a test file the guard missed), the `archive/` of `memory lint`, the `novelty.json` files under a `skill novelty retest` directory, and the skill directory a repo arm installs over (which read as empty, and was removed, when a directory under it could not be listed) report the directory. A tree that can be listed gives the same result, and a listable tree with no `novelty.json` keeps its message.
