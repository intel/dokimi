### Fixed

- **`arms run` and `arms matrix-run` print each cell's model, harness and plugin set at setup.** The setup log now has a `Model: <provider>/<model>, harness: <harness>` line and always a `Plugins:` line, `Plugins: none` for a cell without plugins. Before, the model and harness were not printed and a no-plugin control cell had no `Plugins:` line, so the control could not be told from the log.
