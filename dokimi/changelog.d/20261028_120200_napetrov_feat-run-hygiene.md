### Added

- **Errors are counted apart.** An answer whose call failed is tallied as `errors` in `eval reference` summaries and per arm in arms summaries. The arms summary shows an `Errors` column, and the console summary shows the count per arm.

### Changed

- **Earlier figures are not comparable.** Pass rates, deltas and selection rates from runs before this change do not match new ones for the same inputs. Re-run `arms judge` or `eval reference` to compare.
- **Errors leave the scores out.** An answer whose call failed leaves `n`, the pass rate and the mean out. Before, it was lumped with answers that no check could score.
- **Empty replies are errors, never scores.** An empty, whitespace-only or content-less model reply is an error on its cell in arms runs and in `eval reference`: it is never judged or graded (a negated check no longer scores `""` as 1.0), and it leaves `n`, the pass rate and the mean out.
- **Judge failures with an empty message are counted.** A judge exception whose message is empty (for example `TimeoutError()`) is recorded under its class name, so it is counted as an error rather than dropped.
- **`delta_vs_baseline` is paired.** It is the mean of arm minus baseline over the questions both arms scored. Before, it was the difference of two unpaired means, so a question one arm errored on moved the delta.
- **A judge failure is an error, not a pass.** In `eval reference`, a judge check whose call raised, or whose reply had no YES or NO, makes the answer an error: no score and no pass, even when its other checks passed. Before, the other checks decided the pass. The block carries `error`, as an arm cell does.
- **An empty reply stays an error when an output shaper adds text.** Emptiness is read on the model's reply before the shapers run, so a footer on an empty reply is not an answer.

### Fixed

- **Empty replies and content-less replies are not cached.** Neither is stored in or replayed from the response cache, so a later run asks the model again.
- **`delta_vs_baseline` is always present.** It is `null` when no question has both arms scored, instead of missing.
