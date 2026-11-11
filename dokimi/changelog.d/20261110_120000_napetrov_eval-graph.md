<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Added

- **`eval graph --artifact SKILL --out graph.json`** builds the need graph `eval coverage --graph` scores against. It makes one model call and sends only the skill's name and description, never the body. It writes `need_graph.v1`, every node marked `confirmed_by: hypothesis`, with `provenance` set to the model, the graph prompt version and `draws: 1`. The graph is frozen: an existing `--out` is refused with exit 2. A reply with no usable node exits 1 and writes nothing (#331).
