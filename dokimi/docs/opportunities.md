---
depends_on:
  - dokimi/dokimi/opportunities.py
  - dokimi/dokimi/commands/opportunities.py
  - dokimi/dokimi/llms_txt.py
---
<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

# One queue of fixes: `report opportunities`

`report opportunities` reads findings that dokimi has already stored and ranks them
into one list of fixes a person or a coding agent can work through. No model call.

```bash
python cli.py report opportunities results/novelty/acme.json static.json \
    catalog.json judge_validity.json docs/llms.txt --top-n 5
python cli.py report opportunities ... --format json --out opportunities.json
```

## Inputs

Each path is detected by its content:

| Input | Produced by | Becomes |
|---|---|---|
| novelty artifact | `novelty` | degraded, still-missing and redundant atoms per model |
| `repo_static.v1` | `repo scan` | failing and unresolved claims, one item per file |
| `catalog_eval.v1` | `catalog check --json` | error and warning findings, one item per skill and code |
| `judge_validity.v1` | judge validity | a judge that fails or cannot settle its gate |
| any other text | an `llms.txt` file | the static llms.txt checks (title, summary, when to use, when not to use, link descriptions, size) |

A file that is none of these, or a `.json` file that does not parse, is an input error.

## Each item

- `impact` (0 to 1): the share of the measured signal the fix addresses. A regression
  (degraded atoms), a catalog error and a failed judge count more than a gap or a warning.
- `effort`: `low`, `medium` or `high`.
- `quadrant`, from impact and effort: `act_now`, `plan`, `if_capacity`, `deprioritize`.
  Items are sorted by quadrant, then impact, then effort.
- `source_key`: a hash of what the problem is, not of its counts, so the same problem
  keeps its key across runs and a tracker can follow it.
- `execution_prompt`: a short instruction for a coding agent. It ends by asking the agent
  to re-run the dokimi check that produced the finding, so the fix is confirmed by
  dokimi's evidence rather than by the agent.

The JSON output is an `opportunities.v1` artifact
([schema](https://github.com/intel/dokimi/blob/main/dokimi/dokimi/schemas/opportunities.v1.json)),
so `runs list` and `runs show` see it.

## Limits

Impact compares findings within one kind well, and across kinds only roughly: it is a
triage order, not a measurement. The command reads only what is on disk; re-run the
producing check to see whether a fix worked.
