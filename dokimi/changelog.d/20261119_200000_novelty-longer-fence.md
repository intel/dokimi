### Fixed

- **`skill novelty` keeps a four-backtick code block whole** — a fence of four or more backticks or tildes was closed by a three-character fence line inside it, so the rest of the block was split into extra sentence spans. A fence now closes only on a line that starts with its whole opening run. Span counts and ids change for artifacts that use longer fences (#974).
