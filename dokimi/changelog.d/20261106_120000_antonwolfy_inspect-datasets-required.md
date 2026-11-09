### Documentation

- **The Inspect importer's docstrings name `datasets` a required dependency** — the docstrings
  of its `hf_dataset(...)` loader in `dokimi.task_formats.imports.inspect_ai` still called
  `datasets` optional, though `pyproject.toml` lists it under `[project.dependencies]`. No
  behaviour changes.
