---
name: dokimi-fix-opportunities
description: Pull the top dokimi findings (report opportunities) into the current repo and fix them, then show before and after. Use when asked to act on a dokimi report, fix what dokimi found, or work through its act-now queue. Not for designing evals or running a benchmark.
---
# Fix the top dokimi opportunities

`report opportunities` ranks stored dokimi findings (novelty artifacts,
`repo_static.v1` outputs, llms.txt files) into one queue. Each item has a
`source_key` that stays the same across runs of the same finding, a
`quadrant`, and an
`execution_prompt` written for you.

## Workflow

1. **Before.** Build the queue from the finding files you were given and keep it:

   ```bash
   python cli.py report opportunities static.json --format json --out before.json
   ```

2. **Pick.** Take the first three items (lowest `rank`) whose `quadrant` is
   `act_now`. If none is `act_now`, stop and report that; do not widen the pick.
3. **Apply.** For each item, follow its `execution_prompt` as the task: the
   smallest change to the affected file. A repository claim target includes the
   report path and the repo-relative file after it. Open that file in the source
   checkout. Verify unresolved claims before editing. A novelty target points
   to the skill analyzed. Touch nothing
   the prompt does not name. If a fix needs a decision only the user can make,
   skip the item and say why.
4. **Re-check.** For repository findings, rerun the repository’s `static` entry point. For
   novelty findings, rerun `skill novelty analyze` with the original settings. For
   llms.txt findings, use the edited index directly when rebuilding the queue:

   ```bash
   python cli.py report opportunities static.json --format json --out after.json
   ```

5. **Report** one row per picked item: `source_key`, title, what you changed,
   and whether the key is gone from `after.json`. A key still present is
   *not fixed*, whatever the edit looked like; if its `evidence` lists fewer
   lines than before, say *partly fixed*. For indistinguishable repeated rows, compare
   the number of occurrences of that key as well. List keys that appear only in
   `after.json` as possible regressions and check each against your diff
   (novelty re-runs call a model and vary between runs).

Do not edit the finding files or `before.json` by hand to make a key disappear.
