### Fixed

- **A file path with a lone high surrogate no longer stops the reach token** — `token` in `repos/reach.py` hashes `key_bytes` of its text, so a path such as `"n\ud800"` (a `\ud800` escape in JSON) gets a token of its own instead of a `UnicodeEncodeError`; the token of every path that encoded before is unchanged. `Bundle.measured_digest` in `novelty/bundle.py` hashes its path rows the same way.
