### Added

- **Question sets are written as `questions.v2`** — every writer (`questions generate`,
  `questions refine`, `catalog trigger-set --format questions` and the `evaluate` /
  `benchmark` pipeline's `questions/<product>.json`) stores the text under `question`
  instead of v1's `text`, stamps `"schema_version": "questions.v2"` and validates the set
  before writing it; a set that fails is kept at `<name>.rejected` before any answer is
  paid for. Scripts that read `.text` from these files need `.question`
  (`dokimi/schemas/questions.v2.json` documents every field).

### Changed

- **Question sets are read tolerantly, and a read never rewrites an input** — `v1` sets
  (curated golden sets included) and older hand-written shapes (a bare list,
  `question_id` / `question_text`, a list `metadata`, records with no `id`, `null` in a
  field v1 leaves open) keep loading in the pipeline, `arms run`, `answers generate`,
  `report generate`, `questions refine`, `eval checklist` and `dataset export`. A set that
  needed more than the `text` → `question` rename logs one
  `read <path> in legacy questions format (...)` warning; fix the file by hand to stop it.
  A `--questions-from` or `--custom-questions` file is no longer written over, even when
  it is the run's own `questions/<product>.json` (the one exception is a Python-API
  `force_regen=True` merge that adds records to that file); the run writes its own v2 copy.
  `report generate` and `questions refine` print `Error: ...` and exit 1 on an unusable set
  instead of a traceback; `questions refine --fill-gaps` validates the cleaned set before
  any gap-fill model call, so an invalid set costs nothing.
- **`question_set_hash` changes for sets whose records had no `id` key** — such records now
  take their `question_id`, or are numbered `q0000`, `q0001`, ... by position, so the hash
  of such a legacy set differs from earlier runs and their results are not paired with new
  ones. Sets valid as v1 or v2 keep their ids and hash.
