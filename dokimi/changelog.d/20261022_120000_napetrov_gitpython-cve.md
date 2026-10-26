### Security

- **The main lock pins gitpython 3.2.1**, which fixes CVE-2026-114145 (`pip-audit` failed on 3.2.0).
  The fix is newer than the 7-day `--exclude-newer` window, so the lock's header command now
  exempts gitpython (and the already hand-resolved certifi and pillow) with
  `--exclude-newer-package`. Drop each of the gitpython, certifi and pillow exemptions once the
  pinned release (3.2.1, 2026.10.19, 13.0.0) is 7 days old.
