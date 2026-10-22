### Added

- **`repo run --delegation none` for a single-agent baseline.** Under
  `--tools all` Claude Code could start subagents through its `Agent` tool, so
  a `repo run` cell was not a single-agent run. `--delegation none` runs the
  harness profile's new `delegation.none` tool arguments instead (for
  `claude-code`: `Agent`, `Task` and `Workflow` added to
  `--disallowedTools`); a profile without them refuses the flag, and so does a tool mode that
  `delegation.none` does not list (an allow-list, no fallback to the native
  arguments; `claude-code` lists `none`, `read` and `all`). A cell records the
  delegation whose arguments ran: `native` for a tool mode whose arguments are
  the same either way. The default,
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
  the old one as done: the run is refused before any agent starts, and `--force` or a
  new `--batch` runs them. A batch dir that already holds cells of another
  execution is refused before any agent starts (unless `--force`), and so is a
  result from earlier versions, which carries no `execution`: it is not
  reused.
- `--force` is refused when the batch dir holds finished cells outside the run's plan, or of a task the host cannot run (which `--force` keeps), whose execution id differs, so one report row never pools two executions. `--delegation none` does not stop a skill with `context: fork` from starting a subagent.
