### Fixed

- **`python -m dokimi.schema_compat` follows schemas moved out of a directory whose name
  contains a carriage return** — it read git's `-z` rename listing as text, which turns
  each `\r` into a newline, so it then asked git for a directory the base does not have
  and stopped with a git error instead of comparing the schemas. The listing is now split
  on NUL before each path is decoded.
