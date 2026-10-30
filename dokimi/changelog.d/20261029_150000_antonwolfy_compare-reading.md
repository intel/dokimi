### Added

- **`repo run` records each cell's start context, and `repo compare` compares it** — `result.json`
  `agent.first_prompt_tokens` is what the first main-thread model call was sent (input plus cache
  creation plus cache read tokens, one call per message id, as `context trace` reads it): start-up and
  the task. It is `null` for a trace without that call and for the `text` format. `repo compare` adds a
  descriptive `first_prompt_tokens` row; a batch written before the field has no such row.
- **`repo compare --task ID`** — repeatable: compares only those task ids. An id no finished cell of the
  batches has exits 1 and names it; the tasks left out are not listed as unpaired, nor their unfinished
  cells as missing (a cell's task is the one its `meta.json` records), and the `--json`
  summary (`task_filter`) and the text header (`tasks: N of M (--task)`, M the tasks with a finished
  cell) record the selection. Without `--arm`, an arm with no finished cell among the selection is
  not compared, and both name it (`task_filter.dropped_arms`, `not compared, no finished cell among
  the --task selection: <arms>`).
- **Reading notes under a `repo compare` comparison** — a gate or score row of 2+ tasks whose interval
  includes 0 says it is inconclusive and gives `mde`, the smallest effect 80% power detects at that
  spread (`mde` is in every `--json` row). With 5 or fewer changed tasks the sign-flip p cannot go
  below 0.05 (its floor is `2 / 2^k` for k changed tasks), so `mde` is `null` and the note says how
  low p can go instead. A row whose interval and p disagree says so and why
  (`interval_p_disagree`); for gate and score it compares the interval with Holm's `p_holm`, quotes
  it and says it is the one to read, and a descriptive row uses its raw p; descriptive rows with
  p below 0.05 are named as uncorrected and outside Holm's family. No delta, interval or p changes.
