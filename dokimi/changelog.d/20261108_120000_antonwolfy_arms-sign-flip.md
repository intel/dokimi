### Changed

- **Arms significance and its interval come from one sign-flip test** — every paired
  arms result (`arms run` per arm and per interaction contrast, `report model-compare`
  and the with/without eval report) now takes `p` from one two-sided sign-flip test
  on the per-question deltas and its 95% interval from inverting that test, as
  `repo compare` rows do, so the interval excludes 0 exactly when `p < 0.05`. The
  interval is uncorrected; Holm or BH corrects `p` into `p_adjusted`.
  In `arms.json` the `significance` and `interactions` blocks drop `p_ttest`,
  `p_wilcoxon` and `n_wilcoxon` and add `p` and `n_changed` (the pairs whose delta
  is not 0); `ci_method` is `sign_flip_inversion`. `arms.v1.json` is widened in
  place, so an `arms.json` written before still validates, and the arms report
  still renders it (its p column falls back to `p_ttest`, marked `†`, with a note
  under the table that the record predates the sign-flip test).
  Verdicts near 0.05 move, almost always toward significant: the old rule (t test
  *and* Wilcoxon below 0.05) was conservative, about 3% false positives at a
  nominal 5% on simulated judge deltas, against 5% now. A constant shift now gets
  a p and a verdict (eight +20 deltas: p 0.0078) where it had none; Cohen's d_z
  stays undefined there. Fewer than 6 pairs now get a p too: `significant` is
  `None` where the floor `2 / 2**k` over the k changed pairs is not below 0.05,
  and both interval ends are open (`null`) at 5 or fewer pairs.
  `report model-compare`'s table has one sign-flip p column and a changed-pairs
  column instead of the t and Wilcoxon columns, and its `t_stat` is gone.
  `sign_flip_p`, `min_sign_flip_p`, `ShiftTest`, `sign_flip_interval`, `within`
  and their constants (with one `ALPHA`) moved from `dokimi.repos.compare`,
  `dokimi.repos.compare_interval` and `dokimi.repos.compare_reach` to
  `dokimi.eval.sign_flip`: code that imported them from `dokimi.repos` imports
  them from there. `repo compare` output is unchanged.
  In the Python API, a `confidence` other than 0.95 now raises `ValueError` where
  it was accepted, since the interval inverts the test at 0.05: in `paired_test`
  and `arm_significance` of `dokimi.eval.stats` and in `interaction_test` and
  `interaction_significance` of `dokimi.eval.interactions`, for any row with 2 or
  more pairs. `dokimi.eval.stats.MIN_PAIRS_FOR_TEST` is gone, and so is the
  `HAS_SCIPY` of `dokimi.report.model_compare` and `dokimi.report.eval_report`
  (`dokimi.eval.stats.HAS_SCIPY` stays).
