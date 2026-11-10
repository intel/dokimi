### Fixed

- **A provider call with no credentials fails at once** — litellm raises a missing
  `OPENAI_API_KEY` as `InternalServerError` and a missing Azure key as `APIError`; a missing
  `GEMINI_API_KEY`, and Vertex with no Application Default Credentials, no project or no
  `google-auth` installed, come as `APIConnectionError`. Every such call was retried three
  times, 14 s of backoff before the same error. These errors are now raised on the first
  attempt; a rate limit, a 5xx or a connection error is still retried.
