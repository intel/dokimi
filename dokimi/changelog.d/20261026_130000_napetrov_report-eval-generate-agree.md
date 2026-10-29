### Changed

- **`report generate` and the pipeline summary average each arm over the complete pairs, as `report eval` does** — the
  Overall table, the topic and source breakdowns, and the `stats` block of `--format json` used
  to average the context arm and the baseline arm over every question scored in each, so the two
  means could cover different questions and their difference was not the delta printed beside
  them. The `pipeline` run did the same for the summary it prints and stores in
  `steps.evaluation.summary`. All of them now cover only the questions scored in both arms, and
  the delta is the difference of those means. For an eval file with an unpaired question, the
  context and baseline numbers change; for a file the judge writes, the delta does not. The report
  says how many questions are unpaired (`paired_questions` and `unpaired_questions` in the JSON,
  and a note plus the Count cell in the Markdown). To see the new numbers, run `report generate`
  on an existing eval file again.
  The top and bottom performer tables of `report generate` rank the complete pairs too.

### Fixed

- **`report eval` computes the delta from unrounded means** — the Delta was the difference of the
  two arm means after each had been rounded to one decimal, so it could be 0.1 away from the
  difference of the unrounded means. It is now the unrounded difference rounded once for display,
  so it can move by 0.1 in the Summary, Breakdown and Difficulty tables. The arm means themselves
  print as before. Where questions are unpaired, the Count cell now shows them, and a note says the
  means are over the complete pairs.
  The win rate and the degradation rate in Key Observations divide by the complete pairs, not by
  every question, so they can rise for a file with an unpaired question.
