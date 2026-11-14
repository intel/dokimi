<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Fixed

- **The `url:` and `context7` doc caches and the topics cache no longer replay what they should
  not have stored** — a `url:` page cut at the page size limit was cached as if whole and served
  at any other limit; it is now cached per limit (pages cached before are fetched once more). A
  Context7 answer under 100 characters (an error stub, an empty answer) is returned but no longer
  cached, and one stored before is fetched again. `eval questions generate` no longer caches an
  empty topic list, the generic fallback topics or topics taken from such a stub, and no longer replays the topics extracted from
  one `--doc-source` for another. An unreadable cache entry (not UTF-8, cut JSON,
  the wrong shape) is a miss with a warning naming the file, not a crash, and is overwritten;
  entries are written whole (tmp file, then rename), and a failed write is a warning.
