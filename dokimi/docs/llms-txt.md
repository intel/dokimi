<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

# Check an llms.txt index

[Feature card](features/discovery.md#49-check-an-llms-txt-index)

`llms.txt` is the agent-facing index of a docs site: the first file an agent reads to decide
whether a tool fits the task and where to look next. `dokimi docs llms-txt` checks it
statically. It makes no network or model call.

```bash
dokimi docs llms-txt site/llms.txt          # a file
dokimi docs llms-txt site/                  # a directory with llms.txt at its top
dokimi docs llms-txt site/ --json           # cli_output.v1 on stdout
dokimi docs llms-txt site/ --token-budget 4000
```

## Checks

| Check | Severity | What it wants |
|---|---|---|
| `title` | fail / warn | One `# <Product name>` H1 (warn on more than one) |
| `summary` | fail | A `> one-line summary` blockquote under the title |
| `links` | fail / warn | Markdown links to the docs; warn on a list link with no `: description` |
| `when_to_use` | warn | A `## When to use` section |
| `when_not_to_use` | warn | A `## When not to use` section naming the alternative |
| `size` | warn | At most `--token-budget` estimated tokens (default 2000) |

## Output and exit code

Text output is one tab-separated row per finding (`severity, check, file:line, message, fix`;
line 0 means the whole file), then a verdict line. With `--json` the findings are under
`data.findings` and as the subject's `findings`; `verdict` is `fail` or `pass`.

The exit code is 1 when a `fail` finding exists and 0 otherwise, so a docs CI job can gate on
it. Warnings do not fail the run. A missing file is an input error (exit 2).

To turn the findings into a ranked list of fixes for a coding agent, pass the same file to
`dokimi report opportunities`.
