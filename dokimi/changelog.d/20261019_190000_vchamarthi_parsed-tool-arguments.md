### Fixed

- **The agent loop accepts tool-call arguments that are already parsed** — a
  provider that returns `function.arguments` as a dict no longer aborts the
  loop with a `TypeError`. A non-object value is recorded as `malformed_args`,
  and a call with a null `function` is recorded as an unknown tool. The
  assistant turn replayed to the model names that call `unknown`, not null.
