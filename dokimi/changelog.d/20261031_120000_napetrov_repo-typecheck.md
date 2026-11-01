### Added

- **`dokimi:typecheck` built-in grader and `repo scan --typecheck` (Python fences).** The grader type-checks the
  task's listed files with the image's pinned mypy and passes when none has an error (`tool`, `version`, `paths`
  in `task.json`; a mismatched version is a grader failure). `repo scan --typecheck [mypy|basedpyright]` checks
  each Python fence of the guidance against `--python-env`'s site-packages: a fence with an error is a `fail`
  stale-API candidate, a fence that does not parse or has no interpreter is `unresolved`. The report gains a
  `typecheck` block, `null` without the flag. See `docs/repo-evaluation-contract.md` §4.5 and §8.2.
