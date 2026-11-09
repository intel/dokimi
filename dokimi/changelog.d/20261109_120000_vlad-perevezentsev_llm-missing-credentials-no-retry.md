### Fixed

- **A provider call with no credentials fails at once** — litellm raises a missing
  `OPENAI_API_KEY` as `InternalServerError`, a missing Azure key as `APIError` and Vertex
  without Application Default Credentials (or without `google-auth` installed) as
  `APIConnectionError`, so every such call was retried three times, 14 s of backoff before the
  same error. These errors are now raised on the first attempt; a rate limit, a 5xx or a
  connection error is still retried.
