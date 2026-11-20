### Fixed

- **`skill novelty` keeps a four-backtick code block whole** — a fence of four or more backticks or tildes was closed by a three-character fence line inside it, so the rest of the block was split into extra sentence spans. A fence now closes, as in CommonMark, only on a line holding nothing but a run of its character at least as long as the opening one, so ```` ```python ```` inside a block is content; a backtick line with another backtick on it (```` ``` `x` ``` ````) no longer opens a fence. Span counts and ids change for artifacts with such lines (#974).
