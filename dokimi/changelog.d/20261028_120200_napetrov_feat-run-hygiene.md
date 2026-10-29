### Fixed

- **Errored answers are counted as errors, not as unscored.** An answer whose call failed is tallied as `errors` in `eval reference` summaries, and as `errors` per arm in arms summaries. Errors leave `n`, the pass rate and the mean out. Before, they were lumped with answers that no check could score.
- **Empty replies are errors, never scores.** An empty, whitespace-only or content-less model reply is an error on its cell in arms runs and in `eval reference`: it is never judged or graded (a negated check no longer scores `""` as 1.0), and it leaves `n`, the pass rate and the mean out.
- **Judge failures with an empty message are counted.** A judge exception whose message is empty (for example `TimeoutError()`) is recorded under its class name, so it is counted as an error rather than dropped.
- **Empty replies and content-less replies are not cached.** Neither is stored in or replayed from the response cache, so a later run asks the model again.
- **`delta_vs_baseline` is paired.** It is the mean of arm minus baseline over the questions both arms scored. Before, it was the difference of two unpaired means, so a question one arm errored on moved the delta.
