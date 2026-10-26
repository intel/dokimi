# Coverage report

dokimi has no command that writes this report (`python -m dokimi.coverage`
prints a product x awareness/work task count, not scenario coverage). Write
it by hand, in the suite's README and in the PR that adds or changes the
suite.

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
