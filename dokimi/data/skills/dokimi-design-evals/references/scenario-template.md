# Scenario template

One scenario per task directory. Fill every field; write `none` rather than
leaving one out, so a reviewer can tell "considered, empty" from "forgotten".

```yaml
id: <lower-kebab-case, equals the task directory name>
status: draft            # draft | reviewed | holdout
source:                  # where this scenario comes from; hypotheses say so
  kind: issue            # user-task | issue | pr | fixed-failure | workflow | constraint | negative | inferred
  ref: <URL, trace id, or doc path + section>
  confirmed_by: none     # a person who confirmed it is real user work; `none` = hypothesis
initial_state: >
  The repository at <full SHA>, plus any setup patch (injected bug, missing
  file). What exists, what is broken, what the agent can and cannot see.
request: >
  The exact prompt the agent gets, worded as a user would word it. No hint of
  the grader, the expected diff or the skill under test.
expected_outcome: >
  The end state that counts as success, stated so a check can decide it.
allowed_actions: >
  What the agent may do. Name the alternatives when more than one valid way
  exists, so none of them is graded as a failure.
forbidden_consequences: >
  End states that fail the task however the outcome looks: tests deleted or
  skipped, a criterion weakened, files outside scope changed, a claim of
  "tests pass" with no run, a forbidden tool used.
verifier:
  kind: deterministic    # deterministic | rubric (calibrated judge + human review)
  checks:                # one line per check, each tagged with its layer
    - outcome: <what the test asserts>
    - process: <only if truly mandatory; else omit>
    - cost: <reported metric; gated only with a declared budget>
mutations:               # verifier self-test controls that fit this task (verifier-self-test.md)
  - skipped-step
valid_inaction: false    # true when declining or doing nothing is a correct answer
steps: none              # multi-step: list checkpoints, each with its own verifier
reproduce: >
  The commands that build the image and run oracle and null, with the SHA.
```

## Where the fields go in each task format

| Field | Harbor task (`tasks run`) | Repository task (`repo run`) |
|---|---|---|
| request | `instruction.md` | `task.json` `prompt` |
| initial state | `environment/Dockerfile` + seed files | `task.json` `base`, `setup_patches` |
| expected outcome, forbidden consequences | `tests/test.sh` + `tests/test_*.py` | the repository's `grade` entry point, `task.json` `gate` |
| reference solution | `solution/solve.sh` | the `oracle` entry point |
| multi-step checkpoints | `task.toml` `steps` (Harbor runs them; the per-step self-test is manual) | not supported |
| status, source, allowed actions, mutations | not read by dokimi | extra `task.json` keys are passed to the grader unread |

`status`, `source` and `mutations` have no field dokimi reads today. Keep them
in the task's `task.toml` `[metadata]` table, or as extra `task.json` keys,
and in the suite's README, so the record travels with the task.

Format references, in the dokimi project: `docs/contributing-terminal-bench-task.md`
(Harbor) and `docs/repo-evaluation-contract.md` §3 (`task.json`).
