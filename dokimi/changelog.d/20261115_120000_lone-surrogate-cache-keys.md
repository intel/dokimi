### Fixed

- **`repo reach` without `--salt` no longer stops for a checkout whose path has a non-UTF-8 byte** — the default salt is hashed from the label's UTF-8 bytes (`surrogateescape`, `key_bytes` in `utils.py`), so a surrogate-escaped directory name gets a salt instead of a `UnicodeEncodeError`; the salt of a UTF-8 label is unchanged.
