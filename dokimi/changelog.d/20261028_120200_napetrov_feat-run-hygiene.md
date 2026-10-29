### Fixed

- **Errored answers are counted as errors, not as unscored.** An answer whose call failed is tallied as `errors` in `eval reference` summaries, and as `errors` per arm in arms summaries. Errors leave `n`, the pass rate and the mean out. Before, they were lumped with answers that no check could score.
- **Empty replies are not cached.** An empty or whitespace-only model reply is neither stored nor replayed from the response cache, so a later run asks the model again instead of replaying an empty answer as a failure.
