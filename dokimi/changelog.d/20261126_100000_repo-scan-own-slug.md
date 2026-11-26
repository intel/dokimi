<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Fixed

- **`repo scan` takes a code span naming the repository's own `owner/name` as `ok`** — a guidance line such as ``pushes to `main` in `octo-org/demo` `` gave a `path` claim that was always `unresolved` (`tried octo-org/demo`), since the name was tried as a path and only a used Action is accepted as an `owner/name`. A span equal to the `owner/name` of a GitHub `source.url` (letter case ignored, a `.git` suffix or trailing `/` of the URL allowed) is now `ok` with the reason `this repository (source.url)`, unless the tree holds a path of that name, which is judged as a path as before. Another repository's `owner/name`, a manifest with no `source.url` and a non-GitHub URL keep their verdicts.
