<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Fixed

- **Pairwise judge reprompts an unparseable reply at pinned sampling** — with
  `temperature` or `seed` set, a redraw of the same prompt returned the same
  non-answer, so three identical replies lost the verdict. `PairwiseJudge` now
  takes `pinned=True` for that case and adds a reminder to re-draw in the
  required output format before each redraw (its own text, not the JSON reminder
  of the single and panel judges). The `eval` CLI does not set it yet, so its
  runs are unchanged. Unpinned judges are unchanged.
- **Pairwise and checklist reruns replay stored redraws** — a rerun of a
  pairwise or checklist judgement now replays the stored redraw instead of
  asking the first draw live again. Stored replies are not changed; the rerun
  makes fewer requests.
