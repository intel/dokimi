# Coverage report

Write this report by hand, in the suite's README and in the PR that adds or
changes the suite. `python -m dokimi.coverage` prints only a product x
awareness/work task count.

`python cli.py eval coverage <evals> --graph need_graph.json` adds the part
a script can check. It needs a need graph (what users of the subject need,
built from its name and description only) and a `needs` list on each eval
that names the graph nodes it grades. From those two files alone it lists the
core and rare_critical nodes that no eval names, and the relevance of each
eval: `orphan` for `needs: []`, `unlabelled` for no `needs`. Paste its
Markdown under the table below. It does not replace the areas in the table,
and a node the graph never had is still a gap nobody looked at.

## Template

```markdown
## Coverage: <subject> at <SHA>, suite <name>

| Area | Scenarios | Status | Notes |
|---|---|---|---|
| Happy path | fix-null-deref | reviewed | |
| Branches (each supported variant, platform, option) | none | | GPU path untested: no device in CI |
| Recovery (agent hits an error and must recover) | build-fails-first | draft | |
| Ambiguity (request needs a question or a stated assumption) | none | | |
| Constraints (forbidden tools, files, versions) | no-network-install | reviewed | |
| Negative tasks (subject should decline or not apply) | unrelated-css-fix | reviewed | valid inaction |
| Neighbouring tasks (close to the subject, should not trigger it) | none | | |

Hypotheses not yet confirmed by a user: <ids, with their source>.
Not covered, by decision: <area: why>.
Self-test: <commands run, result>. A pass shows the verifiers are sane on
null, oracle and the listed mutations; it does not show relevance or coverage.
```

## Rules

- Every row has scenario ids or `none`. `none` with no note is a gap nobody
  looked at; `none` with a note is a decision.
- Count a multi-step scenario once, however many checkpoints it has.
- List `draft` scenarios separately from `reviewed` and `holdout` ones; a
  draft does not cover anything yet.
- Name each hypothesis (a scenario inferred from code, docs or issues and not
  confirmed by a user) with its source.
