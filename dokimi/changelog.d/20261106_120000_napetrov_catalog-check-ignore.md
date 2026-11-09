### Added

- **`catalog check --ignore CODE`** (repeatable) leaves the findings with that code out
  of the scan: they are not printed and do not set the row's state or the `--strict`
  exit. A noisy check, such as `name-mismatch` on a directory with a synthetic name,
  no longer fails a scan. An unknown code is a usage error that lists the codes. With
  `--jsonl --resume`, rows written under other `--ignore` codes are reviewed again.
