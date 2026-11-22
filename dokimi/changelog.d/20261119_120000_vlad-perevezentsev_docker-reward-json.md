<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Fixed

- **`docker-*` harnesses read a verifier's `/logs/verifier/reward.json`** — they copied out only
  `reward.txt`, so a Harbor verifier that writes `reward.json` alone (every uxlfoundation/skills
  answer-quality task) was graded by its exit code: exit 0 errored the cell ("wrote no
  /logs/verifier/reward.txt"; before #695 it scored 1.0), a non-zero exit scored 0.0. Such a cell
  now records the verifier's reward, partial credit such as `0.58` included, so its
  `results[].reward`, `pass_rate` and arm deltas change. As in Harbor 0.24.0, `reward.json` is
  read when there is one, else `reward.txt`; it must be a JSON object of finite numbers, and its
  reward is the `reward` key, or the only key. One that is a directory or not such an object, has
  several keys and no `reward`, or a reward outside [0, 1], errors the cell, also beside a
  `reward.txt`; a `docker cp` of it that fails errors the cell too, and so does either file when
  it is not UTF-8 (a bad byte is no longer replaced before parsing). A link is followed in the
  container (`docker cp -L`), and a dangling one counts as no `reward.json`. A `command:` harness
  takes a `reward.json`'s reward by the same rule, and still reads any `reward.txt` under its run
  dir first: `{"group_score": 0.2, "reward": 0.9}` is 0.9 (it was 0.2, the first labelled number),
  and a bare `0.5`, or several keys without `reward`, now errors the cell (`no verdict: exit N,
  reward file has no reward`) where it scored 0.5 or the first labelled number; write `{"reward":
  x}`. A directory named `reward.txt` or `reward.json` under its run dir is such a file with no
  reward, where it raised `IsADirectoryError`. A Harbor-backed cell whose trial `result.json` has
  one reward under another name (`"rewards": {"accuracy": 0.5}`) now scores it, as Harbor does,
  instead of erroring. See `docs/coding-harnesses.md`.
