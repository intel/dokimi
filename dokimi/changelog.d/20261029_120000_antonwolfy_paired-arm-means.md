### Fixed

- **The dashboard and `report model-compare` average each arm over the complete pairs, as
  `report generate` does** — the dashboard's per-library and per-source context and baseline
  means, the context and baseline columns of the `report model-compare` Overall Summary and
  its ranking, and the context column of its per-difficulty rows, used to average each arm over
  every question scored in it, so the means could cover different questions than the delta
  beside them. They now
  cover only the questions scored in both arms, and the dashboard's delta is the difference of
  those means rather than the mean of the stored per-question `delta`. For a run with a question
  scored in one arm only, the arm means change, and on the dashboard the status band that
  follows the context mean can change too. A run that scored one arm only still shows that
  arm's mean; a run, source group or difficulty row with both arms scored but never on the same
  question shows no means.
