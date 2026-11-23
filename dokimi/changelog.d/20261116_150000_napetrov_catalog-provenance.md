<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Added

- **`catalog provenance` matches each skill copy to the upstream version it came from** — offline, from the `--upstream` git checkouts or `--upstream-manifest` files you name (dokimi ships no upstream list). A copy is matched by its git blob sha against the upstream history, then by frontmatter name and word Jaccard similarity ≥ 0.8. The report gives the origin skill and version, how many commits and months behind upstream head, and whether the copy was edited.
