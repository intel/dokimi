### Changed

- **`repo scan` reports a bare identifier that is not in the tree as `unresolved`, not `fail`.** A code span such as `ImportError`, `mkl_random` or `PyBUF_ND` can name a builtin, an external API or a sibling package, so its absence from the tracked files is not proof that the guidance is stale. Paths, link targets and commands that do not resolve stay `fail`, so `--fail-on=fail` still stops on a missing file. The claim stays in the `--json` rows with its reason (#851).
