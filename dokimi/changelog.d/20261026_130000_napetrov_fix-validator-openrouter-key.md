### Fixed

- **`dokimi questions generate --validate` no longer sends `OPENROUTER_API_KEY` to a provider other than OpenRouter** — with
  `--provider google-vertex`, the question validator passed the OpenRouter key as the Google key, so validation used the
  wrong key. The validator now falls back to `OPENROUTER_API_KEY` only for `openrouter`, as the judge and the question
  generator already do. `google-vertex` now uses `GEMINI_API_KEY`. `--provider openai-codex` no longer receives that key
  either. No command or flag changes. If you validated with `google-vertex` or `openai-codex` while `OPENROUTER_API_KEY`
  was set, rotate that OpenRouter key.
