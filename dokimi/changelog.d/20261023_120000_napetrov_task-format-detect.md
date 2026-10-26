### Added

- **`tasks inspect <path> [--json] [--out FILE]`** — detects the eval-task
  format of a file, a task directory or a whole tree and reports its
  structure: kind (executable, answer-only, trigger, localization), item
  count, present and missing parts, lint problems and which dokimi backends
  consume it today. Recognises Harbor, BenchFlow `task.md`, terminal-bench v1,
  SWE-bench instances, Inspect AI samples (JSONL and CSV) and `@task` files
  (scanned as text, never imported), OpenAI evals JSONL and registry specs,
  skill-creator `evals.json` and trigger sets, skill-reach query sets,
  promptfoo configs and test files, dokimi question sets and ExploreBench
  tasks; anything else is reported as `unknown` with its files. A dataset
  file a config names (an Inspect `FieldSpec` remap, an OpenAI evals
  model-graded spec, promptfoo `tests: file://`) is read as that config says,
  and a git-LFS pointer stub is reported as one. `--json` and `--out` write
  the `task_inspect.v1` artifact. Detection only: no new format runs yet.
