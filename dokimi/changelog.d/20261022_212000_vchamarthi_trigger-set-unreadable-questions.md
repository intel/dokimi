### Fixed

- **`catalog trigger-set`** prints `Error: --questions <path>: <reason>` and exits 2 (bad
  input, not a failed gate) when the `--questions` file is missing, a directory, not valid
  JSON, not UTF-8 or nested too deeply, instead of ending in a Python traceback. The read
  goes through a shared `read_json_input` helper whose `InputError` `cli.main` reports the
  same way for any command.
