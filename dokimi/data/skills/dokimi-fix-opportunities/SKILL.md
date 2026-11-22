---
name: dokimi-fix-opportunities
description: Pull the top dokimi findings (report opportunities) into the current repo and fix them, then show before and after. Use when asked to act on a dokimi report, fix what dokimi found, or work through its act-now queue. Not for designing evals or running a benchmark.
---
# Fix the top dokimi opportunities

`report opportunities` ranks stored dokimi findings (novelty artifacts,
`repo_static.v1` outputs, llms.txt files) into one queue. Each item has a
`source_key` that stays the same across runs *of the same input paths*, a
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
   smallest change to the file `target` names (for a novelty item, the skill
   the artifact was built from), in that file's own conventions. Touch nothing
   the prompt does not name. If a fix needs a decision only the user can make,
   skip the item and say why.
4. **Re-check.** Re-run the command that wrote each input file (same flags,
   writing to the same path), then rebuild the queue with the input paths
   spelled exactly as in step 1; the key hashes the path, so `./static.json`
   or a renamed file changes every key:

   ```bash
   python cli.py report opportunities static.json --format json --out after.json
   ```

5. **Report** one row per picked item: `source_key`, title, what you changed,
   and whether the key is gone from `after.json`. A key still present is
   *not fixed*, whatever the edit looked like; if its `evidence` lists fewer
   lines than before, say *partly fixed*. List keys that appear only in
   `after.json` as possible regressions and check each against your diff
   (novelty re-runs call a model and vary between runs).

Do not edit the finding files or `before.json` by hand to make a key disappear.
