<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Changed

- **Judge and panel retry warnings come from `dokimi.llm`** — the judge and
  panel redraw loops now run `llm.ask_parsed`. The warning text for an
  unparseable reply is unchanged, but its logger is `dokimi.llm`, not
  `dokimi.eval.judge` / `dokimi.eval.panel`, so a log filter keyed on those
  names must change. A single judge whose three draws all fail to parse now
  raises `llm.Unparseable` (a `ValueError` subclass) with the same message. It
  used to raise the parse error itself, which for a reply that parses to a JSON
  array was an `AttributeError`. Scores, redraws, cache keys and usage are
  unchanged.
