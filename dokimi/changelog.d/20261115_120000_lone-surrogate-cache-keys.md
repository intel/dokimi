### Fixed

- **`repo reach` without `--salt` no longer stops for a checkout whose path has a non-UTF-8 byte** — the default salt is hashed from the label's UTF-8 bytes (`surrogateescape`, `key_bytes` in `utils.py`), so a surrogate-escaped directory name gets a salt instead of a `UnicodeEncodeError`; the salt of a UTF-8 label is unchanged.
- **A library id or query with a lone surrogate no longer stops the Context7 response cache or the topics cache** — `Context7Client.get_library_docs` and `RagasSeedExtractor.extract_topics` hash the key through `key_bytes`, so a `local:` doc source whose directory name has a non-UTF-8 byte, or a question with a `\ud800` JSON escape, gets a key instead of a `UnicodeEncodeError`; the key of a UTF-8 library id and query is unchanged.
