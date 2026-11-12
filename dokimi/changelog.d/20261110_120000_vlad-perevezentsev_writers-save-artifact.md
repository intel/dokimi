### Fixed

- **Personas, question reviews and the `report eval` Q&A sidecar carry the run envelope** —
  `eval personas discover` (the personas file and `--save-analysis`), `eval questions review`,
  `eval questions analyze` and the `.qa.json` beside `report eval`'s markdown wrote plain JSON:
  no `schema_version`, no `run_id`, not atomic, so `runs list` did not see them. They are
  saved through `save_artifact` now, as the new kinds `personas`, `persona_analysis`,
  `question_panel`, `question_quality` and `report_qa`, and the `evaluate` pipeline's personas
  as `personas` too. A payload its schema refuses is not written: it is kept at
  `<name>.rejected` and the command exits 1 with `Error: <kind> artifact rejected: …`. A model
  reply of another type is not refused: a persona field that should be text (an integer `id`)
  is made text and one that should be a list a one-item list, and `eval questions analyze`
  reads a difficulty that is not text as `intermediate` and says so in the question's `error`.
  `eval questions review` saves the whole report after every question, so a run cut short
  leaves a valid file. Personas and question reviews still go to `personas/` and `reports/` by
  default, outside the `runs list` root.
