### Fixed

- **`dokimi questions generate --validate` no longer sends `OPENROUTER_API_KEY` to a provider other than OpenRouter** — with
  `--provider google-vertex`, the question validator passed the OpenRouter key as the Google key, so validation used the
  wrong key. `OPENROUTER_API_KEY` is now used only for `openrouter`, as the judge and the question generator already do.
  `google-vertex` uses `GEMINI_API_KEY`: set it, or Vertex credentials, or the validation call fails and each question is
  kept unscored. No command or flag changes. If you validated with `google-vertex` while `OPENROUTER_API_KEY` was set,
  rotate that OpenRouter key.
