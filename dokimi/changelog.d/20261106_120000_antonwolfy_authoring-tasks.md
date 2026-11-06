### Added

- **`repo tasks authoring` builds authoring tasks from merged pull requests** — review replay measures
  guidance for reviewing; these tasks measure it for writing code. From the same kind of file
  `gh api graphql` writes (with `body` and `createdAt`), one `authoring_pr<number>/` task per merged PR:
  `base` is the merge base of its head with `mergeCommit^1`, the prompt is its title and description
  without fenced blocks that quote the change, links to its commits or diff, and lines that name a
  hidden test (counted in `prompt_dropped`; more than 5 skip the PR), its added or changed test files
  go to `hidden/`, and the rest of its diff, without guidance files and `eval_dir`, is the oracle in
  `reference/`. A PR with no test file, only test changes, more than `--max-lines` (default 400) lines
  outside its tests, or a fast-forward merge is listed with the reason. `public_at` is the PR's
  `createdAt`, so `repo compare` flags these tasks as it does replay tasks. No LLM, no GitHub call;
  exits 1 when no PR gave a task or the input cannot be used (contract §4.7).
- **Built-in grader `dokimi:hidden_tests` and the manifest's `entry_points.test`** — the framework copies
  a task's hidden files into the workspace, runs the repository's test command on them (`{tests}`; it
  writes a JUnit report to `{out}`), and grades `pass` when every hidden test ran and passed, with
  `pass_share` beside it for `repo compare --metric pass_share`. `eval check` runs the null and the
  oracle steps of such a task twice each and reports a task whose two runs differ as `flaky`, which
  exits 1 like a broken one (contract §2, §4.3, §4.5).
