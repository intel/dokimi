### Security

- **The locks pin filelock 4.2.4**, which fixes the thirteen filelock 3.32.7 advisories
  that `pip-audit`, osv-scanner and dependency review ignored. Every litellm release still
  declares `filelock<4.0` (its main has lifted it), so both lock headers pass
  `--override=requirements-overrides.txt` and every install of `requirements.lock.txt`
  passes `--no-deps`. No advisory is ignored anymore. Drop the override and `--no-deps`
  once a litellm release allows filelock 4.
