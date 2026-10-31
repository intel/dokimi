---
depends_on:
  - dokimi/dokimi/llm_cli.py
  - dokimi/dokimi/commands/backends.py
---
<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

# Subscription backends: a logged-in CLI as the LLM provider

The Q&A track (answers, judging, question generation, personas, novelty) can call
a model through a coding-agent CLI the machine is already signed in to, instead
of an API key. Today one backend exists, `claude-cli` (Claude Code's `claude -p`).
Pass it wherever a command takes `--provider` or `--judge-provider`, except `tools` (it needs tool calling) and the arms that `arms run` and `arms matrix-run` refuse for a CLI provider (see the table).

## Check what this machine can use

```bash
python cli.py backends          # one row per backend: installed, version, signed in, how
python cli.py backends --json
```

This calls no model. A backend that is not signed in prints how to fix it. The login is
probed with `--setting-sources ""`, as a call runs: calls ignore settings files, so a
Bedrock route that only `~/.claude/settings.json` sets is reported as not signed in, and a
login or token next to it is reported as that login. On a machine without a browser,
`claude setup-token` prints a token; export it as `CLAUDE_CODE_OAUTH_TOKEN`.
`benchmark preflight` checks the login the same way, instead of looking for an API key.

## Run

```bash
python cli.py answers generate --product oneTBB --questions questions/oneTBB.json \
  --output answers/oneTBB.json --provider claude-cli --model haiku
python cli.py eval score --product oneTBB --answers answers/oneTBB.json \
  --output eval/oneTBB.json --judge-provider claude-cli --judge-model sonnet
```

`--model` takes what `claude --model` takes: an alias (`haiku`, `sonnet`,
`opus`) or a full id (`claude-sonnet-5-5`). The default judge model works on
`claude-cli` as it does on `anthropic`. When the answerer and the judge are the
same model, the judge grades its own answers. With `--provider claude-cli`, a
model that is not a Claude alias or id is refused at parse time.

## How it differs from an API provider

| | API provider | `claude-cli` |
|---|---|---|
| Credential | `*_API_KEY` | the CLI's own login |
| `--temperature`, `--seed` | sent when set | refused before the run (`unsupported_sampling_params`) |
| `max_tokens` | output ceiling | ignored: the CLI has no flag for it |
| Hidden prompt | none | fixed CLI preamble (≈0.5k–1.3k tokens, by CLI version), plus working directory, git status, platform and date |
| Tools | `chat(tools=...)` | refused: one prompt, one reply |
| Arms that offer tools (`mcp_agent`, `skill_agent`, `skill_catalog`, a `combined` arm containing one, a plugin wrapping one), and `--session-turns`, in `arms run` and `arms matrix-run` | a tool loop or a conversation | refused before the first answer is paid for |
| `cost_usd` | litellm price | `None`: the CLI's `total_cost_usd` is list price, not what a subscription is charged |
| Latency | model only | plus ≈2–3 s CLI start-up per call |

What this means for results:

- **Don't pool scores across transports.** `claude-cli:sonnet` and
  `anthropic:claude-sonnet-5-5` get different prompts (the CLI adds its own text)
  and different sampling. Compare arms within one backend.
- **The CLI states its environment, and no option tested here turns this off.**
  It does so with no tools and with its own system prompt replaced.
  `--exclude-dynamic-system-prompt-sections` moves those sections into the first
  user message, but `claude --help` (2.1.304) says it applies only to the default
  system prompt, which dokimi replaces, so dokimi does not pass it. Tested and not working:
  `CLAUDE_CODE_HIDE_CWD`, `CLAUDE_CODE_DISABLE_GIT_INSTRUCTIONS` (Claude Code
  2.1.304). dokimi runs every call in one fixed empty directory
  (`$XDG_CACHE_HOME/dokimi/cli/claude-cli`, default
  `~/.cache/dokimi/cli/claude-cli`), so the stated environment is the same in the
  with-docs and the without-docs arm, and the response cache still hits. A
  without-docs answer may still mention it ("the working directory isn't a git
  repository").
- **Repeats differ.** With no temperature or seed, the same prompt gives a
  different answer each time; measure variance with repeats, not a pinned seed.

## Isolation

Each call is `claude -p --input-format text --output-format json --model <m>
--tools "" --setting-sources "" --strict-mcp-config --no-session-persistence
--system-prompt-file <f>`. The user prompt goes on stdin and the system prompt in
that file (a private temp file, removed after the call), so neither is bound by
the argument limit, which Linux sets at 128 KiB per argument. Without these flags, a plain `claude -p`
in a repository carries about 37k tokens of agent prompt, tool schemas and `CLAUDE.md`.

- `--bare` is never used: it reads only `ANTHROPIC_API_KEY`, so a subscription
  login fails with "Authentication error".
- The child gets the caller's environment minus every variable starting with
  `ANTHROPIC_`, `CLAUDE_` or `CLAUDECODE`, except `CLAUDE_CONFIG_DIR`,
  `CLAUDE_CODE_OAUTH_TOKEN` and `CLAUDE_CODE_GIT_BASH_PATH` (Git Bash on Windows). That drops the session variables, an API key or
  token that would win over the login, and a base URL, model alias or
  Bedrock/Vertex switch, so the CLI runs on its own login. If dokimi itself
  runs inside Claude Code and the session variables were passed on, the child
  would join the parent's session. Git is stopped at the work dir's parent
  (`GIT_CEILING_DIRECTORIES`), so it finds no repository above it; a `GIT_DIR` or
  `GIT_WORK_TREE` the caller sets is dropped, since it would name one.
- The CLI keeps its own config directory, because that is where the login is
  stored. User settings and `CLAUDE.md` stay out because of
  `--setting-sources ""`.

## Errors and retries

Rate limits, overload, 5xx and a timeout are retried with the same backoff as
an API provider. An unknown model, a missing login, a missing binary, an unusable
work directory and a subscription usage limit fail on the first attempt. A usage
limit resets in hours, so a retry does not help.

## Terms of use

A consumer subscription is for personal use. Keep CI and shared benchmark
runs on API keys.
