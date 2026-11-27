### Fixed

- **Release check respects comments and fences in `CHANGELOG.md`**: `scripts/check_release.py` no longer counts a bullet inside an HTML comment or a fenced code block as a release entry, and `--notes` no longer stops at a `## ` heading inside a fenced example. This affects maintainers cutting a release (#PR_NUMBER, #1290).
