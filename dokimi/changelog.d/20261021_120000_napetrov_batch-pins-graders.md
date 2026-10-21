### Changed

- **A batch is pinned to the built-in grader sources too.** `repo run` hashes the `dokimi:` graders into `_eval/batch.json` (`graders`) next to `eval_dir` and `image`, and refuses to resume a batch after they change, as it does for an edit to `.agent-eval/grade.py`; `repo compare` warns when two batches ran from different graders. Before, a grader change moved scores (for example `replay_mul`) between cells of one batch with no warning. A batch stored before this change has no `graders` pin and is refused on resume: start a fresh `--batch` dir.
