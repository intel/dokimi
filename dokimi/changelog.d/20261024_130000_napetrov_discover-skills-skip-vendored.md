### Fixed

- **Vendored skills no longer load** — skill discovery (the `skill-catalog:` arm and the `catalog` commands) does not walk `.git`, `node_modules`, `site-packages`, `.venv`/`venv`, `.tox`/`.nox`, `.hg`/`.svn` or `__pycache__` below the catalog root, so a dependency's copy of a skill is no longer offered as a skill of the catalog.
