### Security

- **The `dev` extra requires pytest 9.0.3 or later**, which fixes PYSEC-2026-1845; the old
  `pytest>=7.4.0` floor let a fresh install resolve an affected pytest. The lock already
  pinned 9.1.1. `osv-scanner.toml` lists the two advisories SECURITY.md documents
  (PYSEC-2026-2447, PYSEC-2026-3046), the same IDs `pip-audit` ignores in CI, so
  osv-scanner and OpenSSF Scorecard report the same baseline.
