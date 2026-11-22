---
name: dokimi-fix-opportunities
description: Pull the top dokimi findings (report opportunities) into the current repo and fix them, then show before and after. Use when asked to act on a dokimi report, fix what dokimi found, or work through its act-now queue. Not for designing evals or running a benchmark.
---
# Fix the top dokimi opportunities

`report opportunities` ranks stored dokimi findings (novelty artifacts,
`repo_static.v1` outputs, llms.txt files) into one queue. Each item has a
`source_key` that stays the same across runs, a `quadrant`, and an
`execution_prompt` written for you.

## Workflow

1. **Before.** Build the queue from the finding files you were given and keep it:

   ```bash
   python cli.py report opportunities static.json --format json --out before.json
   ```

2. **Pick.** Take the first three items (lowest `rank`) whose `quadrant` is
   `act_now`. If none is `act_now`, stop and report that; do not widen the pick.
3. **Apply.** For each item, follow its `execution_prompt` as the task: the
   smallest change to `target`, in the file's own conventions. Touch nothing
   the prompt does not name. If a fix needs a decision only the user can make,
   skip the item and say why.
4. **Re-check.** Re-run the command that wrote each input file (the same
   `repo scan`, novelty or llms.txt check, same flags), then rebuild the queue:

   ```bash
   python cli.py report opportunities static.json --format json --out after.json
   ```

5. **Report** one row per picked item: `source_key`, title, what you changed,
   and whether the key is gone from `after.json`. A key still present is
   *not fixed*, whatever the edit looked like. List new keys that appear only
   in `after.json`: they are regressions your change caused.

Do not edit the finding files or `before.json` by hand to make a key disappear.
