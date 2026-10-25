### Added

- **`tasks inspect <path> [--json]`** — detects the eval-task format of a file,
  a task directory or a whole tree and reports its structure: kind
  (executable, answer-only, trigger), item count, present and missing parts,
  lint problems and which dokimi backends consume it today. Recognises Harbor,
  BenchFlow `task.md`, terminal-bench v1, SWE-bench instances, Inspect AI
  samples and `@task` files (scanned as text, never imported), OpenAI evals
  JSONL, skill-creator `evals.json` and trigger sets, skill-reach query sets,
  promptfoo configs and dokimi question sets; anything else is reported as
  `unknown` with its files. Detection only: no new format runs yet.
