### Added

- **`--provider claude-cli` / `--judge-provider claude-cli`**: answers, judging,
  question generation, personas and novelty can run through a logged-in Claude
  Code CLI (`claude -p`) instead of an API key. Each call runs with no tools, no
  user or project settings and no MCP servers, in one fixed empty directory, with
  the prompt on stdin. A requested `temperature` or `seed` is refused, since
  the CLI cannot honour them. `cost_usd` is `None`, because the CLI's figure is
  list price. Rate limits, overload and timeouts are retried; an unknown model or
  a missing login is not. See `docs/subscription-backends.md`.
- **`backends`**: lists the CLI backends with install, version and login status;
  calls no model. `benchmark preflight` checks a CLI backend's login instead
  of an API key.

### Changed

- The `--provider` choices of every command come from one tuple,
  `dokimi.llm.ANSWER_PROVIDERS`. `tools` keeps API providers only, since it needs
  tool calling.
