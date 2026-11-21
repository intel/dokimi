<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Fixed

- **`data import docling` reruns replace their own files** — a rerun into the same `--out-dir`
  wrote `<stem>-<ext>-2.md` beside the file the first run wrote, so the docs dir filled with
  copies. The output directory now keeps `.dokimi-docling.json` (`docling_sources.v1`), which
  names each file's source; a rerun replaces that file atomically, and another source with the
  same name still gets the next number, also across runs. A file the record does not name and a
  link are still never written over. A directory converted before this change gets one more
  numbered copy on its first rerun, then stays put (#687).
- **`eval personas discover` keeps a use-case bullet that names its section** — a README bullet
  such as `- Web applications` under `## Use cases` was read as a section header and dropped,
  and the same bullet outside a use-case section opened one. A list item now needs a marker (`-`, `*`, `•`) and a space, so a line such as
  `*Note*: x` or `-item` is no longer an item (#687).
- **Docker harness cells: an egress sidecar whose removal failed is no longer taken as gone** —
  a `docker rm` of the sidecar that exited non-zero still marked it stopped, so the verifier ran
  beside it and teardown did not try the removal again. The cell now errors before the
  verifier runs, as for any failed docker step, and teardown retries the removal before it drops
  the network (#687).
