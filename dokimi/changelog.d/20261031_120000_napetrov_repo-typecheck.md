### Added

- **`dokimi:typecheck` built-in grader and `repo scan --typecheck` (Python fences).** The grader type-checks the
  task's listed files with the image's pinned mypy and passes when none has an error (`tool`, `version`, `paths`
  in `task.json`; a mismatched version is a grader failure). `repo scan --typecheck` (with `--typecheck-tool mypy|basedpyright`) checks
  each Python fence of the guidance against `--python-env`'s site-packages: a fence with an error is a `fail`
  stale-API candidate, a fence that does not parse or has no interpreter is `unresolved`. The report gains a
  `typecheck` block, `null` without the flag. See `docs/repo-evaluation-contract.md` §4.5 and §8.2.
- A `dokimi:typecheck` task needs no `reference/` directory: with one, its files are the repair the grade
  reads; without one, the listed files are graded as they are and the self-check runs the null step only,
  reporting a listed path that is missing or a suppression the workspace already has (no run could pass).
- A tool report without its diagnostic fields (for example `{}`) is a tool failure, not a clean run.
- A fence that imports a module the environment lacks, or has without types (no `py.typed`), is `unresolved`, not `fail`,
  unless the module is a standard-library one (`import imp` on 3.13) or a missing submodule of a package the repository interpreter finds (`json.nosuch`);
  the grader reads such an installed module as `Any`, not as an error. A fence whose only errors are
  a statement that needs a function or loop around it (`break`, `continue`, `yield from`, `nonlocal`) is unresolved too.
- The grader refuses a workspace module named as one the image's site-packages provides, as it does a standard-library name,
  except the repository's own package when the image installed it from the workspace directory (`pip install .` there).
- A branch mypy never checks because of `TYPE_CHECKING` (`if not TYPE_CHECKING:`, the `else` of `if TYPE_CHECKING:`)
  in a listed file is a suppression. Version and platform guards and unlisted modules are not guarded (§4.5).
- The grader does not read a dot directory (`.venv`, `.git`) that holds no listed file: its `.pyi` files and links
  outside the workspace no longer fail or refuse a clean grade.
