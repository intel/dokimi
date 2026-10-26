### Security

- **The tools lock pins setuptools 84.2.1**, which fixes GHSA-xmj3-9gf7-h52w (`pip-audit` failed
  on 84.0.0). The fix is newer than the 7-day `--exclude-newer` window, so the lock's header
  command exempts setuptools; drop the exemption once 84.2.1 is 7 days old. The three filelock
  3.32.7 advisories are documented in `SECURITY.md` and ignored: the fix is 4.2.1, and every
  litellm release requires `filelock<4.0`.
