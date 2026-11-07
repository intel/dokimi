<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Added

- **A fidelity golden for docker-claude arms** — `dokimi/harnesses/fidelity.py` records the request the pinned Claude Code (2.1.318) sends for a fixed task, normalized, and `tests/test_fidelity_golden.py` checks each arm's request against it and against the payload audit. A new CI job (`fidelity`) runs it. Refresh the golden with `scripts/refresh_fidelity_golden.py`; see `tests/fixtures/fidelity/README.md`.
