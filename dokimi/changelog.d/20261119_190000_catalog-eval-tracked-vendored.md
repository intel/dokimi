### Fixed

- **`catalog eval --changed-from` no longer selects a skill for a tracked file under `node_modules` or another vendored directory inside its bundle.** Only untracked and ignored files were skipped there; a tracked edit selected the skill, against `docs/external-content.md`. A tracked file in a shared directory such as `tools/node_modules/` still selects every skill.
