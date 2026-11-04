### Changed

- **The `-host` arms' plugin directory now comes from one helper** — `docker_solver` builds the
  skill plugin and passes `--plugin-dir` from the same `_host_plugin_dir`, so the leak-audit test
  checks the path the harness uses. No change in behaviour: it is still `plugin` under the
  workspace root, beside the agent's work tree.
