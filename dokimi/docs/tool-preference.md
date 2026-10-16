# Tool preference: what moves a model's choice between equal tools

`cli.py tools preference` measures how a model chooses between two tools that do
the same job, and how much one change to one of them moves that choice: a
vendor-branded name, an assertive or a social-proof sentence, a longer
description, a "do NOT use for …" clause, or simply its place in the list.

Use it before you tune a skill or MCP tool description to win selection. On the
models measured so far, assertive wording moved the choice share by +0.3 to
+0.45 and a longer description by +0.1 to +0.25. A selection rate measured on
one wording therefore says little about the next one.

## Input: groups

A `tool_preference_groups.v1` file lists *groups*. Each group is one user
request, two tools that serve it equally well (the twins `A` and `B`), and
distractors that do not. A group with `"expect": "none"` is a control: its
request needs no tool, and calling one counts as over-calling.

```json
{
  "schema_version": "tool_preference_groups.v1",
  "groups": [{
    "id": "G01",
    "query": "Find where the retry backoff is configured in the docs for our HTTP client.",
    "twins": {
      "A": {"name": "search_docs", "description": "Search the project documentation and return matching passages.",
            "vendor_name": "acme_docs_search",
            "detail": "Covers the user guide, API reference and changelog.",
            "example": "search_docs(query='retry backoff')",
            "exclusion": "Do NOT use this tool for editing documentation files."},
      "B": {"name": "find_documentation", "description": "Look up passages in the project's reference and user guides."}
    },
    "distractors": [{"name": "read_file", "description": "Read the contents of a file at a given path."}]
  }]
}
```

The twins' `description` is the neutral baseline. The optional fields feed the
factors: `vendor_name` for `vendor_name`, `detail` and `example` for
`long_desc`, and `exclusion` for `exclusion`. A factor a twin has no field for
is skipped for that twin. `cues` overrides the sentence `assertive` and
`social_proof` append, and `system` overrides the system prompt (an empty string
sends none). `domain` is a free-form label copied to every item of the group in
the result, for slicing. The full format is
[`tool_preference_groups.v1.json`](https://github.com/intel/dokimi/blob/main/dokimi/dokimi/schemas/tool_preference_groups.v1.json).

The groups are evaluation content: keep them with the tools they are about, not
in this repository.

## Design

The twins sit at the two ends of the tool list, with the distractors in
between in the order given. Every factor changes one twin (the *treated* one)
and is crossed with which twin is treated and whether it is listed first or
last. The baseline offers both orders. A pair group therefore yields 2 + 4 per
factor items, and a control group 2 + 2 per factor.

Each item is one call with `tool_choice="auto"`, scored on its first turn only,
at the provider's default sampling. Current reasoning models accept only
`temperature=1` (see
[Pinning sampling](benchmarking-and-comparison.md#pinning-sampling)), so the
spread is measured with `--reps` rather than pinned away.

```bash
python cli.py tools preference --groups groups.json --dry-run      # count items and calls
python cli.py tools preference --groups groups.json \
  --model claude-haiku-5-5 --provider anthropic --reps 3 --out-json pref.json
```

## Reading the result

`tool_preference.v1` keeps every call (`results`: the tools called in the first
turn, and an outcome `A`, `B`, `both`, `distractor`, `unmatched` or `none`), the
settings that were sent (`max_tokens`, and the `system` prompt and `cues` with
the defaults filled in) and a `summary`:

| field | meaning |
| --- | --- |
| `factor_effects.<factor>` | treated twin's choice share minus the same tool's share at the same position in the baseline; mean over groups, 95% bootstrap CI that resamples groups |
| `first_twin_share_baseline` | share of the first-listed twin when both are neutral; 0.5 is no position bias |
| `pair_outcomes.both_twins` | share of first turns that called both twins in parallel (counted as 0.5 for each twin in the shares above) |
| `pair_outcomes.distractor`, `.none` | first turns that took a wrong tool, or no tool |
| `pair_outcomes.unmatched` | first turns that called only names the item did not offer (made up, or empty); counted as over-calling on controls |
| `control_over_call.<factor>` | share of control calls that used any tool |
| `same_choice_rate`, `modal_share` | across `--reps`: items whose outcome never changed, and the mean share of the most common outcome |

A call that failed is kept with its `error` and left out of the summary
(`n_errors`); so is a reply cut at `--max-tokens` before any tool call. A reply
cut after its tool call is scored, since only the tool's name counts, and
flagged `truncated` (`n_truncated`).

Example: `claude-haiku-5-5` on Bedrock, 40 pair groups and 8 controls, 3 reps
(2928 calls, $0.53):

```text
first-listed twin share (baseline): +0.48 [+0.40, +0.56] (groups=40)
  assertive     +0.44 [+0.39, +0.48] (groups=40)
  exclusion     +0.05 [+0.01, +0.08] (groups=40)
  long_desc     +0.20 [+0.13, +0.28] (groups=40)
  social_proof  -0.02 [-0.06, +0.02] (groups=40)
  vendor_name   -0.01 [-0.07, +0.05] (groups=40)
pair items: both twins 0.278, distractor 0.0114, no call 0.0345
controls, any tool called: assertive 0.0, baseline 0.0, exclusion 0.0, long_desc 0.0, social_proof 0.0, vendor_name 0.0
same choice on every rep: 0.8576 (modal share 0.9498)
```

Two cautions:

- The probe scores one call per item, the way a single-call selection proxy
  does. On a real harness the direction held, but the size was smaller: for a
  skill whose "use when" sentence was removed, a one-call proxy showed −0.42
  and Claude Code −0.20. Confirm a large effect on the harness you ship to.
- Score choices from the calls, not with an LLM judge. A judge that sees the
  conversation but not the tool definitions cannot see a description effect at
  all.
