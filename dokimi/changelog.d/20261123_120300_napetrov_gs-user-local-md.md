### Fixed

- **`path-missing` no longer reports a user's own `*.local.md` file** — a skill that tells the user to create `references/secrets.local.md` (or any `*.local.md` under a bundle folder) names a file that is absent from the bundle by design. Such a span is no longer checked, so it gets no warning. A plain `references/secrets.md` the skill names and the bundle lacks is still reported.
