### Security

- **The locks pin certifi 2026.10.19 and pillow 13.0.0**, which fix GHSA-6q48-6c7w-9gw2
  (certifi) and eight pillow CVEs that `pip-audit` reported on 2026-10-20. Both fixes are
  newer than the 7-day `--exclude-newer` window, so they were resolved by hand with a
  per-package override; the header commands are unchanged.
