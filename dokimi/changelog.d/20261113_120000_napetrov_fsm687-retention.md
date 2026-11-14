<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Fixed

- **`--lexical-only` no longer poisons the retention cache** — an ambiguous-band atom answered without an adjudicator is a placeholder, so it is no longer stored. A later run with an LLM adjudicator used to replay it as a final verdict. The cache version moves to `retention-cache-v5`, so entries already on disk are not read; the old files can be deleted. In the other direction, a `--lexical-only` run no longer reads the cache, so it does not replay a verdict an adjudicated run stored and its output no longer depends on the cache directory.
