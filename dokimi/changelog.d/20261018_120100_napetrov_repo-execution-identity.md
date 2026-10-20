### Added

- **`repo run --delegation none` for a single-agent baseline.** Under
  `--tools all` Claude Code could start subagents through its `Agent` tool, so
  a `repo run` cell was not a single-agent run. `--delegation none` runs the
  harness profile's new `delegation.none` tool arguments instead (for
  `claude-code`: `Agent`, `Task` and `Workflow` added to
  `--disallowedTools`); a profile without them refuses the flag. The default,
  `--delegation native`, keeps today's behaviour.

### Changed

- **A `repo run` cell is reused only by the same execution.** `meta.json` and
  `result.json` gain `execution` (`id`, `tools`, `delegation`); the `id`
  hashes the profile's name, command template, tool arguments, environment
  (`env_set`, `env_allow`), config (`config_dir_env`, `config_files`), prompt
  suffix, `egress`, `bridge`, the SHA-256 of the harness executable and harness
  version (the executable's content, not its path). Rerunning a batch with
  another `--tools`, `--delegation`, edited profile, harness binary, or harness
  upgrade no longer counts cells from
  the old one as done: those cells are reported as crashed, and `--force` or a
  new `--batch` runs them. Results from earlier versions count as
  `--delegation native`.
