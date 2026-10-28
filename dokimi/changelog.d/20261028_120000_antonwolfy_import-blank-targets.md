### Fixed

- **`questions import` leaves a blank judge assert, an empty `contains*` list
  and a punctuation-only Inspect `exact` target out of the grading** — a
  promptfoo `llm-rubric`/`factuality` (or other judged) assert with a blank or
  missing value, or a `contains*`/`icontains*` assert with an empty list,
  imported as a check that `eval reference` refuses, which aborted the run for
  every question of the file; it now imports as `unsupported`, listed and not
  counted, like a blank value already did. An Inspect `exact` target that
  SQuAD-normalises to nothing, such as `...` or `the`, imported as a value no
  answer can equal; it is now dropped, and a sample left with no target
  imports as `unsupported`.
