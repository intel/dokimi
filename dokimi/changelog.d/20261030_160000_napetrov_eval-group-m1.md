### Changed

- **The evaluation commands live under `eval` (#94 M1)** — `personas`, `questions` and the
  evaluation leaves of `tasks`, `catalog` and `repo` are now under `dokimi eval`, and the
  `eval` leaves are grouped into `eval score`, `eval judge`, `eval questions`, `eval personas`
  and `eval triggers`. Every old path still answers, but it prints `moved: dokimi <new argv>`
  to stderr and exits 2 without running anything, so a script fails loudly and names the new
  command. Move the scripts to the new paths before the next release. The arguments are
  unchanged. `dokimi repo tasks replay`, `repo run`, `repo report` and `repo compare` stay where
  they are.

  | Before | After |
  |---|---|
  | `tasks inspect` | `eval inspect` |
  | `repo tasks check` | `eval check` |
  | `tasks list` | `eval tasks list` |
  | `personas discover` | `eval personas discover` |
  | `personas approve` | `eval personas validate` |
  | `questions generate` | `eval questions generate` |
  | `questions analyze` | `eval questions analyze` |
  | `questions refine` | `eval questions refine` |
  | `questions panel-review` | `eval questions review` |
  | `questions import` | `eval import` |
  | `catalog trigger-set` | `eval triggers generate` |
  | `eval score` | `eval score pointwise` |
  | `eval pairwise` | `eval score pairwise` |
  | `eval checklist` | `eval score checklist` |
  | `eval panel-score` | `eval score panel` |
  | `eval grounding` | `eval score grounding` |
  | `eval ragas` | `eval score ragas` |
  | `eval reference` | `eval score reference` |
  | `eval judge-validity` | `eval judge validate` |
  | `eval judge-wording` | `eval judge wording` |
  | `eval cross-judge` | `eval judge agreement` |
  | `personas` (bare, or its help) | `eval personas` |
  | `questions` (bare, or its help) | `eval questions` |

  The `[free]`/`[paid]` labels in `--help` follow the commands. The `consumers` labels that
  `tasks inspect` reports (and the `task_inspect.v1` schema enum) name the new paths. The
  `repo tasks check` step of `repo-eval.yml` runs `dokimi eval check`; its job name is unchanged.
