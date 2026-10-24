### Added

- **`questions import <path>`**: imports Inspect AI samples and `@task` files
  (read with `ast`, never imported), OpenAI evals JSONL (graded per its
  registry YAML), skill-creator `evals.json` and promptfoo configs as
  questions.v2 sets. Each question has a `grading` block of format-neutral
  checks (`match`, `exact`, `fuzzy`, `regex`, `choice`, `json`, `judge`,
  `unsupported`) and `prompt_style: verbatim`. The import report lists what
  did not carry over.
- **`eval reference`**: scores `answers generate` or `arms run` output against
  those checks. Deterministic checks need no model. `judge` checks go to the
  checklist judge in both polarities. It writes eval.v1 or an arms artifact.
- **`eval native-import <log>`**: reads an `inspect eval --log-format json`
  log or a `promptfoo eval -o` output as answers.v1 plus eval.v1, so native
  verdicts can be compared with dokimi's on the same completions.
- skill-creator imports set `expected_skill` to the skill the `evals.json`
  belongs to, so the same set also feeds the `skill-catalog:` trigger eval.
- `answers generate --doc-source none` answers the baseline only.
- `tasks inspect` lists `questions import` as a consumer of the answer-only formats.

### Changed

- The answerer sends a question with `prompt_style: verbatim` as it is,
  without the expert-answer wrapper.
- task_inspect.v1 accepts the `questions import` consumer.
- questions.v2 declares `grading` and `prompt_style` (a widening; existing
  files are unchanged).
