### Fixed

- **`repo reach --format receipt` takes budget's start-up rules from the profile, not the harness name** — the receipt gave expected states only when the harness was named `claude-code`. It now applies them when the profile sets `guidance.claude_displaces_agents`, the key `repo budget` already reads, so a custom profile with the key gets expected states and `claude-code` without it gets none. A saved reach file without the profile's declared guidance still goes by the name.
