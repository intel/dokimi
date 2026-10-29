### Fixed

- **The dashboard and `report model-compare` average each arm over the complete pairs, as
  `report generate` does** — the dashboard's per-library and per-source context and baseline
  means, and the context and baseline columns of the `report model-compare` Overall Summary and
  its ranking, used to average each arm over every question scored in it, so the two means
  could cover different questions and their difference was not the delta beside them. They now
  cover only the questions scored in both arms, and the dashboard's delta is the difference of
  those means rather than the mean of the stored per-question `delta`. For a run with a question
  scored in one arm only, the arm means change, and on the dashboard the status band that
  follows the context mean can change too. A run that scored one arm only still shows that
  arm's mean; a run or source group with both arms scored but never on the same question shows
  no means.
