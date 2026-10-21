# Research register

Dokimi's own research is itself a set of experiments, and many of them come back negative or
inconclusive. The register in `research/records/` keeps every one of them findable, so the
next person who wants to try an idea can see what was already tried, what came out of it and
why a direction was kept or dropped.

The register is plain files in this repository: no server, history from git, review through
pull requests, and any agent with file access can read and write it.

## Record types

Each record is one Markdown file, `research/records/<type>/<id>.md`. The YAML front matter
holds the typed fields that `research/record.v1.json` defines; the body is free Markdown for
the setup, result, rationale and anything else a reader needs, after the
license header every file in the repository carries.

| Type | Id | What it holds | Points at |
|---|---|---|---|
| `idea` | `I001` | A question worth answering, its map theme (`P`…`W`) and `open`/`parked`/`closed` status | — |
| `experiment` | `X001` | Hypothesis, what was run, and the `outcome`: `planned`, `running`, `supported`, `refuted`, `inconclusive` or `aborted` | `idea` |
| `finding` | `F001` | What was learned, with `low`/`med`/`high` confidence | `experiments` |
| `decision` | `D001` | `accepted`, `rejected`, `deferred` or `superseded`, and why | `findings`, `ideas`, `superseded_by` |
| `implref` | `R001` | The issue or pull request that carries a decision out, with its own `status` | `decision` |

Three states stay separate on purpose: an experiment's `outcome` (what the evidence showed),
a decision's `state` (what the team chose) and an implementation reference's `status` (what
was built). A closed issue does not tell which of the three happened.

An experiment that turns out wrong is not deleted: set `retracted: true` and say in the body
what was wrong with it. `retracted` means the whole experiment is invalid, so `check` fails
any finding that still cites it; query, export and render keep it, marked. When only part of a
result is withdrawn, keep the outcome, leave `retracted` unset and list the withdrawn claims
under a `## Corrections` heading. Every record has a `date` (`YYYY-MM-DD`) and optional `links` (required on
`implref`), which must be public `github.com/intel/` or `github.com/napetrov/` URLs. The register is public:
leave out internal hosts, paths, names and raw traces, and describe local evidence in words
("local run, 96 runs").

## Working with it

Run from the `dokimi/` project directory:

```bash
python scripts/research_register.py check
python scripts/research_register.py next experiment
python scripts/research_register.py query --type experiment --ref I001 --where outcome=refuted,inconclusive
python scripts/research_register.py query --type decision --where state=rejected
python scripts/research_register.py export > register.json
python scripts/research_register.py render > map.md
```

- `check` validates every record against its schema, its path and id prefix, and every
  reference. `tests/test_research_register.py` runs it in CI, so a broken record fails the PR.
- `next` prints the next free id for a type. Two PRs can claim the same id; `check` reports
  the duplicate on whichever merges second, which renumbers.
- `query` lists the records matching every filter: `--where FIELD=V1,V2` matches any of the
  values, `--ref ID` keeps the records that point at `ID`. An unknown field, a clause
  without `=` or a missing `ID` exits 2. Booleans are spelled as in the records:
  `--where retracted=true`.
- `export` writes all records, the reference edges and the ideas grouped by map theme as one
  JSON document, stamped with the commit, for the plans-and-research map and other views.
  `dirty` is true when `research/` has uncommitted changes, and `render` says so next to the
  commit.
- `render` writes the plans-and-research map from the same tree: a Mermaid mindmap (theme,
  idea, experiments marked by outcome) and a table, as one Markdown file. It is generated on
  demand and not committed; open it in any Mermaid-aware viewer, such as GitHub or an editor
  preview.

## Adding a record

1. `python scripts/research_register.py next <type>` for the id.
2. Write `research/records/<type>/<id>.md`:

    ```markdown
    ---
    id: X012
    type: experiment
    title: Compressed skill keeps step ordering
    date: 2026-10-14
    idea: I004
    hypothesis: A skill cut to 40% of its tokens keeps the ordering constraints.
    outcome: refuted
    ---
    <!--
    Copyright (C) 2026 Intel Corporation
    SPDX-License-Identifier: Apache-2.0
    -->
    ## Setup
    ...
    ## Result
    ...
    ```

3. `python scripts/research_register.py check`, then open the PR.
