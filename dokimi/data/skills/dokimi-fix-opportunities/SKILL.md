---
name: dokimi-fix-opportunities
description: Pull the top dokimi findings (report opportunities) into the current repo and fix them, then show before and after. Use when asked to act on a dokimi report, fix what dokimi found, or work through its act-now queue. Not for designing evals or running a benchmark.
---
<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

# Fix the top dokimi opportunities

`report opportunities` ranks stored dokimi findings (novelty artifacts,
`repo_static.v1` outputs, llms.txt files) into one queue. Each item has a `source_key`, a `quadrant`, and an
`execution_prompt`. Keep each before-and-after comparison tied to the same
source repository or input.

## Workflow

1. **Before.** Build the queue from the finding files you were given and keep it:

   ```bash
   python cli.py report opportunities static.json --format json --out before.json
   ```

2. **Pick.** Take the first three items (lowest `rank`) whose `quadrant` is
   `act_now`. If none is `act_now`, stop and report that; do not widen the pick.
3. **Apply.** For each item, follow its `execution_prompt` as the task: the
   smallest change to the affected file. For a repository claim, `target` is the
   repository-relative file, prefixed by `<repo>:` when the report carries a
   nonempty string `repo` identifying its source repository. Open the file in
   that source checkout and verify unresolved claims before editing. A novelty
   target points to the skill analyzed. Follow the re-check rules in step 4 for
   each item. Skip an item and explain why if its source is ambiguous, its kind
   is outside this workflow, or its fix needs the user's decision.
4. **Re-check.** For repository findings, rerun the repository's `static` entry
   point. Its keys depend on the repository-relative file, the claim status class
   (`fail` or `unresolved`), and `repo` when supplied. Keep `repo` consistent.
   Build separate queues for repositories whose reports lack that identifier,
   since the same file and status can share a key across repositories. For novelty
   findings, rerun `skill novelty analyze` with the original settings only when
   the user explicitly requests the paid model call. Otherwise mark the item as
   *not re-checked*. For llms.txt findings, pass the edited index using the same
   path spelling as in step 1. For example, `llms.txt` and `./llms.txt` produce
   different keys.

   ```bash
   python cli.py report opportunities static.json --format json --out after.json
   ```

5. **Report** one row per picked item: `source_key`, title, what you changed,
   and the result for that source in `after.json`. If the same key is still
   present for a re-checked input, the finding remains. Report partial progress
   when the evidence lists fewer lines or fewer indistinguishable occurrences
   share the key. Check new keys against your diff before calling them
   regressions. Results from novelty model calls can vary between runs.

Do not edit the finding files or `before.json` by hand to make a key disappear.
