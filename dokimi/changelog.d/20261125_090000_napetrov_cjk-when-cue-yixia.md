### Fixed

- **A Chinese description that opens its situation list with `适用于以下` / `如下` / `下列` and a colon now has a when-cue** — `适用于以下任务：…`, `适用于如下情形：…`, `適用於下列任務：…` count as a when clause, so a skill whose description only lists its situations that way is no longer flagged as missing one. A bare `适用于以下平台的工具。` (no colon in the sentence) is still not a cue, and neither is a scope list whose noun follows a 的, such as `适用于以下平台的开发工作：Linux、macOS。`. Nothing to do for a user unless a description was flagged by mistake.
