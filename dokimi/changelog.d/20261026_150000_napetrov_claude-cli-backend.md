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
- A CLI provider refuses an agentic arm (`mcp_agent`, `skill_agent`, `skill_catalog`) and
  `--session-turns` in `arms run` and `arms matrix-run`, before any answer is paid for: the CLI
  runs one prompt with no tools and no conversation.
- A subscription usage limit fails the call at once, not as a retryable rate limit: it resets
  in hours, so a retry does not help.
- A CLI reply records the model the CLI resolved its alias to: `resolved_model` on each usage
  row (answers, judge calls), `judge_resolved_models` in the judge record. The response-cache
  key takes the CLI version, so a new release that moves `sonnet` does not replay the old
  model's replies. The resolved id is known only after a call, so a move the CLI does not
  ship with is not caught.
- Independence checks treat an alias and its full id as one model (`opus` and
  `claude-opus-…`). The novelty default judge on `claude-cli` is another Claude family than the answerer.
- The CLI child process drops `ANTHROPIC_*`, `CLAUDE_*` and `CLAUDECODE` variables from the
  environment (except `CLAUDE_CONFIG_DIR`, `CLAUDE_CODE_OAUTH_TOKEN` and
  `CLAUDE_CODE_GIT_BASH_PATH` (Windows)), so a billing or
  session setting in the caller does not reroute the call. Git is stopped at the work dir's parent. A Bedrock or Vertex route set
  in that environment is dropped too: the CLI then uses its own login, and `backends` reports
  that login, not the route.
