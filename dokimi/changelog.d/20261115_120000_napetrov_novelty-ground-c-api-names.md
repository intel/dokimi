### Added

- **`skill novelty ground` vetoes C API names** — `--header PATH` (glob ok) and `--library PATH`
  (a shared library, read with `nm -D --defined-only`), each repeatable, with `--prefix cblas_`
  (a C identifier ending in `_`, repeatable). An answer naming a prefixed name no header declares
  and no library exports (`cblas_dgemmx`) is vetoed without a judge, and `meta.key_status` is
  `not_found` for a key that names one. Each input is pinned by its sha256. A `--header` or
  `--library` set with no prefixed name is refused, and `--prefix` needs one of them. No MKL
  install is needed: any header set or shared library of the API works.
