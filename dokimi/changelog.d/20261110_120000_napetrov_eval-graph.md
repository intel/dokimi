<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Added

- **`eval graph SKILL --out graph.json [--extract-model PROVIDER/MODEL]`** builds the need graph `eval coverage --graph` scores against. It makes one model call and sends only the skill's name and description, never the body. It writes `need_graph.v1`, every node marked `confirmed_by: hypothesis`, with `provenance` set to the `PROVIDER/MODEL`, the graph prompt's `sha256:` digest and `draws: 1`. The graph is frozen: an existing `--out`, or one created while the model call ran, is refused with exit 2 and left as it was. A failed call or a reply with no usable node exits 1 and writes nothing (#331). A call with no known price under `--max-usd` keeps its paid reply as the graph, with a warning; a filesystem with no hard links is refused before the call.

### Changed

- **The need graph keeps one node per id and reads labels case-blind** — in `eval graph` and `skill novelty analyze` alike, a repeated node id from the model gets a fresh `n<index>-k` id, a node with no id never takes another node's explicit id, and `"Rare-Critical"` or `"Core"` count as `rare_critical` and `core` instead of falling back to `supporting` (#331). Novelty scores can change for the same model reply (a cached reply replays differently), so re-run earlier `skill novelty analyze` results before comparing them.
