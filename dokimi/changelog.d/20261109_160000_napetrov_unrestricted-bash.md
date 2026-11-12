### Added

- **`catalog check` flags `allowed-tools` that grant Bash for every command.** `Bash` with no pattern, or `Bash(*)`, is a new `warn`, `allowed-tools-unrestricted-bash`, with a scoped example such as `Bash(git:*)`. When the same skill also has a `pipe-to-shell` or `permission-bypass-flag` hit, the message says so, since those commands then run with no prompt. Leave the code out with `--ignore allowed-tools-unrestricted-bash`.
- A skill that grants unrestricted Bash and also has a `pipe-to-shell` or `permission-bypass-flag` finding fails `catalog check --strict`; on its own the code stays a warning.
