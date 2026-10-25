<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

# Journey catalog

Each journey below is a user intent and the outcome Dokimi owes the user for
it. Under each one, use cases (`UC-Jnn-nnn`) name concrete situations and
acceptance criteria (`AC-Jnn-nnn`) name what must hold. A criterion is either
measurable (a test or a recorded run can show it) or marked *manual* (a person
judges it, and says so in the issue or PR).

This page states intent. Which criteria are traced to tests, and whether those
tests passed, is in the trace registry and the coverage report: see
[traceability](traceability.md). A command shown here as `python cli.py ...`
exists in the inspected tree; a capability with no command is called
**proposed**, and its absence is part of the record, not an oversight.

J2, J3, J4, J7, J10, J11, J14 and J15 are written out in full. The other journeys record
the agreed intent and their criteria at the level of detail needed to keep that
intent; they are not traced yet.

Branches that apply to most journeys, stated once:

- **No evals, or poor ones.** Hand over to [J7](#j7--create-or-improve-evals)
  before measuring: a number from a task set that does not represent the
  user's work answers a different question.
- **Model or harness unavailable.** Say which, and measure with what is
  available only if the user agrees the result still answers their question.
- **Budget too small.** Report what the budget buys (fewer tasks, fewer
  repetitions, a wider interval) before spending it, and mark the result
  budget-limited, never pass.
- **Run stopped part-way.** Keep what finished, report what is missing, and
  resume without re-running finished cells ([J11](#j11--explain-results-or-recover-a-run)).
- **Versions not comparable.** Different task sets, question sets, graders or
  prompts under one name are not compared silently; the user is warned or the
  comparison is refused, according to the criterion.
- **No improvement.** A measured null or negative effect is a result. Success
  never requires finding an improvement.
- **Unclear result.** Say inconclusive, and name the next experiment that would
  discriminate.

---

## J1 — Start an assessment

**Actor and intent.** A skill author, maintainer, or the agent acting for one,
who does not yet know what to check. "Check this skill and tell me what is
wrong." "Is this repository ready for agents?" Trigger: a new subject, or one
the user has not measured before.

**Starting point and inputs.** A skill directory, a repository checkout, a
guidance file, or a content repository with some evals. The questions that
change the answer: what work the subject is meant to help with; which models and
harnesses matter; how much may be spent.

**Flow.** Run the free static checks that apply to the subject; say what they
can and cannot show; list the unknowns; propose the next measurement (which
journey, which tasks, which variants, what it would cost).

**Branches.** No evals: hand to J7. Subject type not supported yet (a skill
collection, an MCP server): say so and assess the parts that are.

**Outputs.** Initial evidence, a list of unknowns, the scope that was checked,
and a costed plan for the next step. The plan is a recommendation the user can
decline.

**Measurement scope.** Static checks need no model. Anything beyond them is
handed to another journey with its own scope.

**Today.** The pieces exist per subject type: `python cli.py repo scan` for a
repository, `python cli.py novelty routing` and
`python cli.py novelty analyze --estimate-only` for a skill. A single entry
that picks among them, and the costed plan, are **proposed**.

**Connections.** Leads to J2, J3, J4, J5 or J7.

### Use cases and acceptance

#### UC-J01-001 First look at an unfamiliar subject

- **AC-J01-001** The assessment lists which checks ran, which could not run and why, and what each check can and cannot establish.
- **AC-J01-002** The next-step plan names the journey, the task set, the variants and an estimated cost, and the estimate is shown before anything is spent.
- **AC-J01-003** Static findings are not reported as evidence of task impact.

---

## J2 — Improve my skill

**Actor and intent.** A skill author, or their agent. "Make this skill better."
"Why does the agent ignore half of this skill?" Trigger: a skill that
under-performs, is long, or is about to change.

**Starting point and inputs.** The skill (the bundle: `SKILL.md` and what it
references), and ideally the tasks or questions it is meant to help with. The
questions that matter: which models it must work for; which tasks represent its
use; the budget.

**Flow.**

1. Check the skill can be selected at all: does the agent route to it for the
   queries it is for (`python cli.py novelty routing`)?
2. Estimate what the diagnosis costs before running it
   (`python cli.py novelty analyze --estimate-only`).
3. Diagnose: which parts of the skill add knowledge the model lacks, which
   repeat what it knows, which conflict with it
   (`python cli.py novelty analyze`, then `python cli.py novelty view`).
4. Form hypotheses: cut what adds nothing, protect what carries routing and
   negative constraints, add what the tasks need and the skill lacks.
5. Change the skill (`python cli.py novelty shorten` proposes cuts with the
   protected parts pinned).
6. Re-measure the edited skill against the same probes, and on the tasks it is
   meant to help with, against the unedited one.
7. Keep the change, revise it, or reject it, with the numbers.

**Branches.**

- *No tasks for the skill.* Novelty can still be measured, but it does not show
  task impact; hand to J7 for tasks, or state the gap.
- *Budget too small for the probes.* The cheaper tier
  (`python cli.py novelty analyze --tier triage`) records what it gave up.
- *No improvement.* The change is rejected with its measurement; that is a
  valid outcome.
- *Edited skill no longer matches its extracted inventory.* Probing an
  inventory against a changed skill is refused; extract again.

**Outputs.** A list of hypotheses with the evidence for each; the changed skill
or a justified decision not to change it; a before/after measurement on the
same probes and tasks.

**Measurement scope.** The skill version before and after; the probe set or
task set; the model(s); repetitions; the judge and its calibration band; spend.
Novelty differences below the judge band documented in
[skill novelty limits](../skill-novelty.md#limits-to-state-when-quoting-these-numbers)
are not quoted.

**Today.** Steps 1–6 for novelty are available and walked through in
[the novelty walkthrough](../skill-novelty-walkthrough.md). Task-level
re-measurement of a skill exists through arms (`python cli.py arms run`) and
executable tasks (`python cli.py tasks run`). Joining the novelty diagnosis to
the task-level effect in one report, and a recorded keep/reject decision, are
**proposed**.

**Connections.** Uses J7 for tasks and J14 for single steps; feeds J12 (protect
the improvement) and J13 (reassess later).

**Example.** An author's skill is 900 lines. Routing shows the agent picks it
for two of five intended queries. The estimate prices a triage run at a few
dollars. Triage says 60% of atoms are already known to the model. The author
shortens with routing and negative constraints pinned, re-probes the shortened
entry point: the added-knowledge rate holds within the judge band and routing
improves to four of five. The task-level check is still to run; the author
records it as a gap.

### Use cases and acceptance

#### UC-J02-001 Diagnose a skill and measure an edit

- **AC-J02-001** A re-measurement of an edited skill probes the edited entry point with the inventory of the original, so before and after are answered on the same probes.
- **AC-J02-002** Probing an inventory extracted from a different version of the skill is refused, not silently scored.
- **AC-J02-003** A change that shows no improvement is reported as a measured result with its numbers, and the journey ends successfully. *Manual.*
- **AC-J02-004** A novelty result alone is never reported as an improvement in task outcomes. *Manual until a task-level join exists.*

#### UC-J02-002 Re-measure the edit on the skill's tasks

- **AC-J02-005** The edited and the original skill run on the same task set, model and repetitions, and the report gives the effect with its uncertainty. *Proposed as one step; today two separate runs.*

#### UC-J02-003 Improve a skill automatically

A loop proposes edits to the skill, measures each on the skill's tasks and probes, and keeps what the measured reward supports: a reinforcement-style search over the skill text, not over model weights. *Research.*

- **AC-J02-006** The proposer and the trace analyst see only the development split; the confirmation split is never shown to them, and the report states both splits. *Proposed.*
- **AC-J02-007** An accepted edit is confirmed on the confirmation split with fresh trials, and its gain is reported with its interval; a gain inside the noise band is reported as no improvement. *Proposed.*
- **AC-J02-008** Before the loop scores candidates, each verifier is run against gaming answers; a verifier that passes one blocks the loop and is reported. *Proposed.*
- **AC-J02-009** An accepted edit that copies evaluation answers into the skill, or drops a must-hold constraint of the skill's contract, is blocked and named. *Proposed.*
- **AC-J02-010** The loop stops at its spend cap and returns each kept edit as a diff with its measurement, for the author to accept; it never replaces the skill on its own. *Proposed.*

---

## J3 — Enable my repository for agents

**Actor and intent.** A repository maintainer, or their agent. "Make this repo
work well for coding agents." Trigger: agents struggle with the repository, or
the maintainer is adding agent guidance and wants to know it helps.

**Starting point and inputs.** A repository at a commit, with or without guidance
files (`AGENTS.md`, `CLAUDE.md`, skills) and with or without an evaluation tree
(`.agent-eval/`, see the [repository evaluation contract](../repo-evaluation-contract.md)).
Questions that matter: which agent harnesses and models contributors use;
whether Docker is available; the budget for agent runs.

**Flow.**

1. Find obstacles statically: broken links, missing paths, stale commands and
   ids in the guidance (`python cli.py repo scan`).
2. Check the evaluation manifest is valid (`python cli.py repo validate --manifest`).
3. Check the guidance reaches the agent at all (`python cli.py repo reach`),
   see J4.
4. Prepare evals: representative tasks with graders, in the repository's own
   tree (J7).
5. Self-check the tasks before any agent run: the untouched workspace must
   fail, the reference solution must pass, the agent must not see the graders
   (`python cli.py repo tasks check`).
6. Run agents on the tasks with and without the guidance
   (`python cli.py repo run`), resuming without re-running finished cells.
7. Report one batch and compare arms (`python cli.py repo report`,
   `python cli.py repo compare`).

**Branches.**

- *No Docker.* Task runs and the self-check need it; say so and stop after the
  static and reach steps.
- *A task needs network egress.* Refused before any cell runs, because the
  egress proxy is not implemented; the task is reported unsupported, not passed.
- *Run interrupted.* The next invocation runs only the missing cells.
- *Broken grader.* The self-check fails and names the problem; no agent run
  should be spent on that task.

**Outputs.** A list of obstacles; guidance and tasks the maintainer can commit;
per-task, per-arm results on real tasks with their missing cells listed.

**Measurement scope.** Repository commit; task set; arms (for example
`guidance:none`, `guidance:raw`); harness and model; repetitions; grader;
spend per run and per batch; environment (Docker image, network mode).

**Today.** Steps 1–3 and 5–7 are available. Step 4, deriving candidate tasks
from the repository's code, docs, issues and traces, is **proposed**; tasks
derived from code are hypotheses about what users do, not confirmed intent.

**Connections.** J4 for the guidance itself, J7 for the tasks, J12 to keep it
working.

### Use cases and acceptance

#### UC-J03-001 Find what stops agents in this repository

- **AC-J03-001** A static scan of a repository at a commit reports each guidance file's failing checks and writes a versioned readiness artifact; with a fail gate requested, a failing check exits non-zero.
- **AC-J03-007** A static inspection reports, per project root, the instruction files with their scope and activation, the build, test, lint and type checks with how each is known, and launch risks such as hooks, without running anything from the repository; it writes a versioned environment artifact, and with an error gate a blocking finding exits non-zero.
- **AC-J03-008** A context budget lists, per file, what the repository puts into an agent's context at start, lazily, on demand or not at all under a named harness profile, writes a versioned budget artifact, and with a warning gate a warning exits non-zero.

#### UC-J03-002 Prepare the evaluation tree

- **AC-J03-002** An invalid evaluation manifest is rejected with the offending field before any task runs.

#### UC-J03-003 Verify representative tasks with agents

- **AC-J03-003** A repeated run skips cells that already have a result, unless forced, so an interrupted batch resumes without paying twice.
- **AC-J03-004** A task that needs network egress is refused before any cell runs, and no cell is recorded for it.
- **AC-J03-005** A batch report lists the cells that are missing rather than averaging over what finished. *Proposed as an enforced criterion; the behaviour exists.*
- **AC-J03-006** A task whose host requirements this host does not meet is not run: its cells are marked unsupported with the reason, the report lists them as unsupported rather than failed or missing, and a host that meets them runs those cells later.

---

## J4 — Check AGENTS.md, other instructions or installed skills

**Actor and intent.** A maintainer or skill author, or their agent. "Is my
AGENTS.md any good?" "Do agents even read it?" Trigger: new or edited guidance,
or agents that seem to ignore it.

**Four separate claims.** Instructions can be checked for:

1. **Correctness**: the commands, paths, links and ids they name exist.
2. **Reach**: the harness actually loads or reads the file in the situations
   that matter (root, a subdirectory, a file the agent has to open).
3. **Adherence**: the agent does what the instructions say.
4. **Effect**: tasks go better with the instructions than without.

Each is evidence for itself only. A correct file can be unreachable; a reached
file can be ignored; followed instructions can make tasks no better. The
journey reports them separately and never infers one from another.

**Starting point and inputs.** A repository at a commit with guidance files, or
a single file. Questions: which harnesses matter (reach is per harness); which
tasks represent the work (for effect).

**Flow.**

1. Correctness: `python cli.py repo scan`.
2. Reach: `python cli.py repo reach` with a harness profile and model, after
   `--dry-run` to see the runs and budget it plans.
3. Adherence: **proposed**. No command measures it yet.
4. Effect: `python cli.py repo run` with `--arm guidance:none` and
   `--arm guidance:raw` on the same tasks, then `python cli.py repo compare`.

**Branches.**

- *Harness not installed, crashes or refuses.* Reach records an error or a
  refusal for that scenario; it does not count as "not reached".
- *No tasks.* Effect cannot be measured; it is reported missing and the
  journey hands to J7.
- *Budget.* Reach has a per-run budget; effect is a full agent batch with its
  own caps.

**Outputs.** Four separately labelled findings, each with its evidence or with
"not measured".

**Measurement scope.** Correctness: the commit. Reach: commit, harness and
version, model, scenarios, salted tokens. Effect: commit, task set, arms,
model, repetitions, grader, spend.

**Today.** Correctness and reach are available and traced. Effect is partial:
the arms and comparison exist, but no test or recorded run asserts an effect
claim. Adherence is proposed.

**Connections.** Part of J3; feeds J12 (guidance regressions) and J13 (does the
guidance still help a newer model?).

**Example.** A maintainer's `AGENTS.md` names a test command that was renamed;
the scan flags it. After the fix, reach shows the harness reads the root file
but not the one in `src/`. Effect on tasks is still unmeasured: the report says
so rather than implying the fixed, reached file helps.

### Use cases and acceptance

#### UC-J04-001 Check the instructions are correct

- **AC-J04-001** A static scan reports each failing reference in a guidance file by file, writes `repo_readiness.v1`, and with a fail gate requested exits non-zero on a failing check.
- **AC-J04-002** The static scan's output makes no claim about reach, adherence or effect. *Manual.*

#### UC-J04-002 Check the instructions reach the agent

- **AC-J04-003** A reach measurement can be planned without running the harness, showing the runs and budget, and a run writes `guidance_reach.v1` with a per-scenario result.
- **AC-J04-004** A harness that fails or refuses is recorded as an error or a refusal for that scenario, never as the file not being reached.

#### UC-J04-003 Check the agent follows them

- **AC-J04-005** Adherence is measured against stated instructions on tasks that exercise them, and reported apart from reach. *Proposed.*

#### UC-J04-004 Check the instructions improve task outcomes

- **AC-J04-006** With and without the guidance, the same tasks run on the same model and repetitions, and the comparison reports the effect per task with missing cells listed.
- **AC-J04-007** A guidance arm taken from another revision is pinned to the full commit once per batch, so a moved branch starts a new arm and never adds cells to the old one.
- **AC-J04-008** A patch arm is recorded by path and digest, and a patch that does not apply fails that cell's preparation.
- **AC-J04-009** For an arm that adds files, tools or servers, each cell records whether the agent used them; a comparison warns when the treatment used none, and a cell that left no trace is unmeasured, not a non-use.

#### UC-J04-005 Check an installed skill reaches the agent

- **AC-J04-010** A skill installed with no treatment notice runs beside the same skill announced, on the same tasks, model and repetitions, and the report gives both pass rates, their gap and both skill-invocation rates; the gap is withheld when the two arms attempted or lost different cells.
- **AC-J04-011** Each skill cell records whether the prompt announced the skill and how much of its description the agent's skill listing shows. *Partial: the warning for a cut description is not asserted by a test.*
- **AC-J04-012** A zero skill-invocation rate is reported as "the skill tool was not invoked", never as "the skill was not read". *Manual.*

---

## J5 — Assess a third-party skill

**Actor and intent.** Someone deciding whether to adopt a skill they did not
write. "Does this skill do what it claims?" Trigger: a candidate skill from a
catalog or another team.

**Starting point and inputs.** The skill bundle and its stated claims. Questions:
which of the claims matter to the user; which models they use.

**Flow.** Check structure and selectability; measure what it adds over the model
on its own claims (novelty); then, for the claims that matter, hand to J6 to
test it on the user's own tasks.

**Branches.** A structure check that passes says nothing about usefulness. Claims
with no testable task stay unverified and are listed as such.

**Outputs.** Evidence per claim, problems found, where it applies, and the limits
of the assessment.

**Today.** Catalog scanning (`python cli.py catalog scan`), routing and
novelty exist. A per-claim assessment report is **proposed**.

**Connections.** Usually continues as J6.

### Use cases and acceptance

#### UC-J05-001 Check a skill's claims

- **AC-J05-001** The assessment lists each claim with its evidence or with "not verified", and does not certify general usefulness from structure checks.
- **AC-J05-002** The assessment states the models, tasks and probe sets it covered and what it did not cover. *Manual.*
- **AC-J05-003** A catalog scan reports each skill's Agent Skills specification violations as findings, and fails on them only in strict mode. *Proposed.*

---

## J6 — Does this skill work in my use case?

**Actor and intent.** A user with their own work and a candidate skill. "Will
this help on what we actually do?" Trigger: J5, or a skill already adopted and
doubted.

**Starting point and inputs.** The skill; the user's tasks or questions, possibly
none yet. Questions: what the representative tasks are; which configuration they
run today (the baseline).

**Flow.** Adapt existing evals or add tasks from the user's work (J7); run with
and without the skill on the same tasks; compare.

**Branches.** No user tasks: build them first; the skill's own examples are not
the user's use case.

**Outputs.** A numerical with/without comparison on the user's tasks, with its
uncertainty.

**Today.** Arms and tasks exist (`python cli.py arms run`,
`python cli.py tasks run`); adapting evals to a user's work is manual.

**Connections.** Follows J5; uses J7 and J10.

### Use cases and acceptance

#### UC-J06-001 Test a skill on the user's tasks

- **AC-J06-001** The comparison runs on tasks the user supplied or approved, and the report names their source.
- **AC-J06-002** The report gives the effect per task and overall, with uncertainty, against the user's current configuration.
- **AC-J06-003** A skill or a whole catalog can be installed as an arm on the user's repository tasks, and each cell records which skills were used and whether the expected one was selected. *Proposed.*

---

## J7 — Create or improve evals

**Actor and intent.** Anyone whose measurement needs tasks or questions they do
not have, or has evals they do not trust. "Write evals for this repository."
"Are these evals any good?" Trigger: any journey that hits "no evals, or poor
ones"; or a reviewer doubting a result.

**Four separate claims about evals.** An eval set can be:

1. **Valid**: it parses against its schema.
2. **Correctly graded**: the grader fails an untouched workspace, passes the
   reference solution, and cannot be gamed by reading the grader.
3. **Relevant**: its tasks are the work users actually do.
4. **Covering**: the work it does not test is known and listed.

Schema validity does not show grader correctness; a correct grader on
irrelevant tasks measures the wrong thing; relevant tasks with unlisted gaps
overstate what a pass means.

**Starting point and inputs.** A repository or skill, any existing evals, and
optionally signals of real use: interfaces, docs, examples, issues, traces.
Questions: what users do with the subject; which failures matter most.

**Flow.**

1. Validate what exists (`python cli.py repo validate --manifest`).
2. Self-check graders with no model: null, oracle, isolation
   (`python cli.py repo tasks check`).
3. For judged evals, check the judge: does it tell good answers from
   degraded ones (`python cli.py eval judge-validity`)?
4. Propose new tasks from the subject's signals, as hypotheses for the user to
   confirm. **Proposed.**
5. Add negative controls: tasks the subject should not help with, or
   variants that should not change the result.
6. List coverage gaps.

**Branches.**

- *Grader broken.* The self-check names it; fix before any agent run.
- *Task needs egress.* Reported unsupported, not passed.
- *Judge cannot discriminate.* Its scores are not used as evidence until it can.

**Outputs.** Tasks with graders that pass the self-check, negative controls,
and an explicit list of what the evals do not cover.

**Measurement scope.** The self-check runs the null and oracle once per task in
the task's container, without a model. Judge validity runs the judge on
perturbed answers; it has a model cost and a sequential stopping rule.

**Today.** Steps 1–3 are available; the self-check is traced. Steps 4–6 are
**proposed**: no command derives tasks, adds negative controls, or reports
coverage gaps. A passing self-check shows the grader is sane on null and oracle,
not that the tasks are relevant or the set covers the work.

**Connections.** Supports J2, J3, J4, J6, J8, J9, J10, J12.

**Example.** A maintainer adds two tasks. The self-check fails one: the grader
passes the untouched workspace, so the task can be "solved" by doing nothing.
The maintainer fixes the assertion; the second run passes null, oracle and
isolation. The coverage gap (no task touches the build system) is written
down in the PR.

### Use cases and acceptance

#### UC-J07-001 Check task graders are sane before spending on agents

- **AC-J07-001** The self-check fails a task whose grader passes the untouched workspace, and names the null check as the problem.
- **AC-J07-002** The self-check fails a task whose reference solution does not pass its grader, and passes a task where null fails and oracle passes.
- **AC-J07-003** The self-check flags an image that exposes the task tree to the agent, and reports an egress task as unsupported rather than passed.
- **AC-J07-004** A passing self-check is not reported as evidence that the tasks are relevant or cover the subject. *Manual.*
- **AC-J07-008** A review task can be graded by a built-in grader (seeded problems with decoys, or a replay of past review threads) with no grader in the repository; the self-check runs it on null and oracle, an empty or unparseable review scores zero recall rather than unmeasured, and an unknown built-in name is refused.

#### UC-J07-002 Check an existing judge

- **AC-J07-005** A judge-validity run that reaches its sample cap without a decision reports no pass.

#### UC-J07-003 Draft tasks from the subject

- **AC-J07-006** Tasks derived from code, docs or issues are presented as hypotheses for the user to confirm, with their source. *Proposed.*

#### UC-J07-004 State coverage gaps

- **AC-J07-007** An eval set's report lists the capabilities or areas it has no task for. *Proposed.*

---

## J8 — Which models or harnesses can handle this?

**Actor and intent.** A maintainer or team choosing a model for a task type or a
repository. "Which models can do our work?" Trigger: a new model, or a
requirement (latency, on-prem, cost ceiling).

**Inputs.** Task set; candidate models and harnesses; requirements stated as
thresholds; repetitions.

**Flow.** Run the same tasks across models; report success rate and its
interval per model, with the conditions; mark which meet the thresholds.

**Branches.** A model unavailable for some cells: report it as missing, not as
failing. Too few repetitions to separate models: inconclusive.

**Outputs.** Feasibility and reliability per model under stated conditions.

**Today.** Multi-model runs and comparison exist
(`python cli.py report model-compare`, `python cli.py repo compare`); a
threshold-based feasibility verdict is **proposed**.

### Use cases and acceptance

#### UC-J08-001 Compare models on one task set

- **AC-J08-001** Runs on different models are compared only on the same question or task set; a mismatch is reported.
- **AC-J08-002** Each model's result states its repetitions and interval, and a model whose cells are missing is not ranked as failing.
- **AC-J08-003** A cell answered by a model other than the one requested records the model that answered, and report and compare warn; provider-prefix spellings of one model are the same model.

#### UC-J08-002 Compare harnesses on one task set

- **AC-J08-004** The same tasks run under different harness profiles (Claude Code, Codex CLI, Copilot CLI) on the same arms, model and repetitions, each cell records the harness, its version and its execution settings, and harnesses are compared only on the same task set. *Proposed.*
- **AC-J08-005** A run with subagent delegation is labelled as such and never compared as a single-agent baseline against a run without it. *Proposed.*

#### UC-J08-003 Compare code-review agents

- **AC-J08-006** A review task graded by seeded problems or a replayed review reports recall, false positives on decoys and severe findings per harness and model, and a review with no parseable findings counts as a failure, not dropped. *Proposed.*
- **AC-J08-007** A harness's native review mode and the same harness given a review prompt run as separate arms on the same review tasks, and the report names which arm used the native mode. *Proposed.*

---

## J9 — Cheapest suitable model or setup

**Actor and intent.** Someone paying for agent work. "What is the cheapest setup
that is good enough?" Trigger: cost pressure, a new cheaper model.

**Inputs.** Quality constraint; task set; candidate configurations; prices.

**Flow.** Measure quality and cost per configuration; count the cost of
retries and failures, not only of successes; pick the cheapest that meets the
constraint, with its uncertainty.

**Branches.** No configuration meets the constraint: say so. Prices unknown:
cost is reported in tokens and marked unpriced.

**Outputs.** Cost per acceptable outcome, per configuration.

**Today.** Cost and token accounting exist in runs; a cost-per-acceptable-outcome
selection is **proposed**.

### Use cases and acceptance

#### UC-J09-001 Choose a configuration under a quality constraint

- **AC-J09-001** The cost figure includes failed attempts and retries needed to reach an acceptable outcome.
- **AC-J09-002** A configuration is called suitable only if it meets the stated quality constraint within its uncertainty.

---

## J10 — What effect does this change have?

**Actor and intent.** Anyone who changed something an agent depends on: a
skill, guidance, docs, a tool, a model, a harness, a dependency, the
environment. "Did this help, hurt, or do nothing?" Trigger: a change about to
merge or just merged.

**Starting point and inputs.** Two or more configurations that differ in a
declared way; a shared task or question set; repetitions; budget. Questions:
what exactly differs (the declared change); what else might differ (confounds).

**Flow.**

1. Pin the measurement: the same question set (`--questions-root` or
   `--questions-from` on `python cli.py benchmark run`) or task set for every
   variant.
2. Check inputs before spending (`python cli.py benchmark preflight`).
3. Run each variant.
4. Compare: effect, regressions, cost, uncertainty
   (`python cli.py report model-compare`, `python cli.py compare`,
   `python cli.py repo compare`).
5. Report confounds the comparison detected.

**Branches.**

- *Different question sets.* Runs whose question-set hash differs are compared
  with a warning, on the common questions only. The warning is not a refusal:
  the user decides whether the intersection answers their question.
- *Cached questions differ from the golden set.* A pipeline run reuses the
  questions cached in its output directory until `--force-regen`, and warns
  when they differ from the golden set.
- *One arm name, two prompts.* An arm whose prompt digest differs across runs is
  refused: those runs measured different things under one name.
- *No question source.* Questions are generated, and the run warns and names
  `--questions-root`.
- *No effect.* Reported with its interval.

**Outputs.** Effect per variant with uncertainty, regressions, cost, and the
confounds found.

**Measurement scope.** Question or task set (with its hash), variants and what
differs between them, model, repetitions, judge, spend.

**Today.** Two-run and multi-run comparison on a pinned question set is
available and traced. More than two arms run with `python cli.py arms run`.
For repository arms, `repo compare` warns when the compared cells ran from a
different eval directory, image, harness version or resolved model
(AC-J10-008, partial). Declaring an arbitrary change (OS, dependency,
subagents) as a variant of one experiment is **proposed**, as is adaptive
allocation across many variants.

**Connections.** Underlies J2, J4 (effect), J6, J8, J9, J12, J13.

**Example.** A user re-runs a benchmark after editing a doc source, with
`--questions-from` pointing at the earlier run. Both runs answer the same
questions; the comparison shows a gain on two topics, a loss on one, and the
cost of both runs.

### Use cases and acceptance

#### UC-J10-001 Compare configurations on the same questions

- **AC-J10-001** Two runs on one question root answer exactly its golden set and generate no questions; a multi-run benchmark reuses the first run's questions for every later run.
- **AC-J10-002** A run given an earlier run's questions answers those questions and generates none.
- **AC-J10-003** Preflight rejects a missing key, a missing or malformed earlier-run question source, and a question root without a readable golden set, before any API call.
- **AC-J10-004** A run with no question source warns that questions are generated and names the flag that pins them.

#### UC-J10-002 Know when runs are not comparable

- **AC-J10-005** Comparing runs whose question-set hashes differ warns and names the hashes; the comparison continues on common questions.
- **AC-J10-006** Questions cached in a run's output directory are reused over the question root until regeneration is forced, and a cached set that differs from the golden set warns.
- **AC-J10-007** Comparing runs where one arm name carries different prompt digests is refused.

#### UC-J10-003 Declare an arbitrary change as the variant

- **AC-J10-008** An experiment declares what differs between its variants, records the resolved settings of each, and reports differences it did not declare as confounds. *Partial: repository arms only; `repo compare` warns in free text, not as structured confounds.*

#### UC-J10-004 Know whether a result is stable

- **AC-J10-009** Repeated runs of one task report pass@k, pass^k and path stability separately, so a stable success is told apart from an unstable one with the same success rate. *Proposed.*

#### UC-J10-005 See how the agent worked, not only whether it passed

- **AC-J10-010** Each repository cell records tool calls, failed and denied commands and guidance files read, counted over the agent and its subagents. *Proposed.*
- **AC-J10-011** Report and compare show the process counts per arm as descriptive measures, outside the corrected significance family. *Proposed.*

#### UC-J10-006 Measure a change across a multi-turn session

- **AC-J10-012** A session run asks the question and its follow-ups with the history kept, records usage and cost per turn, and reports the session total with the reason the session stopped. *Proposed.*
- **AC-J10-013** A reminder variant differs from its arm only by the reminder added to each user turn, and the comparison reports the per-turn effect, not only the final answer. *Proposed.*

---

## J11 — Explain results or recover a run

**Actor and intent.** Anyone holding a result they do not trust or a run that
stopped. "Why did this fail?" "Can I finish this run?" Trigger: a failed,
partial, budget-stopped or surprising run. It can interrupt any journey.

**Inputs.** A run directory, report or trace.

**Flow.** Classify each missing or failing cell: the task failed; the
evaluation failed (grader crash, judge error); evidence is missing (cell not
run); data are incomparable. Then resume, re-run what is stale, or propose the
experiment that would decide.

**Branches.** A grader crash is an evaluation failure, not an exclusion and not
a task pass. A budget stop is inconclusive.

**Outputs.** A per-cell classification and a recovery or next step.

**Today.** Repository batches distinguish grader failures and missing cells,
and resume (`python cli.py repo run` skips finished cells). A general
explain-this-run command is **proposed**.

### Use cases and acceptance

#### UC-J11-001 Tell failure kinds apart

- **AC-J11-001** A grader crash is recorded as an evaluation failure and counted as a failure, not dropped from the denominator.
- **AC-J11-002** A sample cap reached without a decision is reported as inconclusive, never as pass.
- **AC-J11-005** A check or run with no task, or where every cell is unsupported or errored, exits non-zero as incomplete with its counts, never as a pass. *Proposed.*

#### UC-J11-002 Resume a stopped run

- **AC-J11-003** Resuming runs only the missing cells and keeps the finished ones.
- **AC-J11-004** A resumed run whose stored cell was produced under different execution settings refuses to reuse it unless forced. *Proposed.*

---

## J12 — Prevent regressions in PR and CI

**Actor and intent.** A maintainer guarding a skill, guidance or docs. "Fail the
PR if this gets worse." Trigger: a PR touching the subject.

**Inputs.** A saved baseline; a check budget; a policy (what blocks, what
warns).

**Flow.** Run the budgeted check on the PR; compare with the baseline; apply the
policy; publish the evidence.

**Branches.** Over budget or inconclusive: the policy decides whether that
blocks, and the check never reports pass. Baseline from a different question
set: refuse or warn as in J10.

**Outputs.** A pass, fail or inconclusive decision with the evidence and its
cost.

**Today.** Baselines exist (`python cli.py baseline save`,
`python cli.py baseline compare --strict`), as do static gates
(`python cli.py repo scan --fail-on-fail`). `baseline compare --strict` adds a
non-zero exit only when a model cannot be verified or differs from the
baseline; a regression in the scores is printed and exits 0. Failing on a regression beyond
a threshold is **proposed** (AC-J12-001), and so is configurable blocking policy
across check kinds (AC-J12-002).

### Use cases and acceptance

#### UC-J12-001 Gate a PR on a budgeted check

- **AC-J12-001** A strict baseline comparison exits non-zero on a regression beyond the threshold.
- **AC-J12-002** An inconclusive or budget-stopped check is reported as such, and whether it blocks is the configured policy's decision.
- **AC-J12-003** The reusable CI check runs only the tasks a change touches, adds a smoke set when shared harness files change, and fails on an unknown task name instead of checking nothing.
- **AC-J12-004** A gate on a run whose result lacks planned cells reports it incomplete and names the missing cells; it never passes on the cells that finished. *Proposed.*

---

## J13 — Reassess after a change of time, model or environment

**Actor and intent.** A maintainer whose earlier conclusions may be stale. "Does
this skill still help with the new model?" Trigger: a new model, harness or
dependency version; time.

**Inputs.** The earlier result with its recorded settings.

**Flow.** Re-run the earlier experiment with the changed condition; compare;
decide whether the assistance is kept, adapted, shortened or removed. Novelty
is a quick diagnostic here: knowledge a newer model already has stops adding.

**Outputs.** Whether the earlier conclusion holds, and a keep/adapt/shorten/
remove recommendation.

**Today.** Re-running and comparing exist; replaying an earlier experiment from
its recorded settings is **proposed**.

### Use cases and acceptance

#### UC-J13-001 Re-check an earlier conclusion

- **AC-J13-001** The reassessment runs the earlier task or probe set with only the declared condition changed, and says which other settings differ.
- **AC-J13-002** A recommendation to remove or shorten assistance cites the measurement that supports it. *Manual.*

---

## J14 — Invoke one capability directly

**Actor and intent.** An advanced user, researcher, or agent that knows which
operation it wants. "Just measure the novelty of this skill on this model."
"Show me the context window of this run." Trigger: a specific question, not a
workflow.

**Starting point and inputs.** Exactly the inputs that operation needs, given
explicitly; nothing inferred from a wider workflow.

**Flow.** Estimate the cost where there is one; run the one operation with its
settings; get structured output and its limits; re-render saved output without
paying again.

**Branches.**

- *Spend cap reached.* The operation stops at the cap; a call with no known
  price stops a capped run rather than spending blind.
- *Saved intermediate output from a different input.* Refused.

**Outputs.** Structured output (JSON with a versioned schema where one exists),
the settings that produced it, the spend, and the stated limits.

**Today.** Direct operations:

| Operation | Command | Notes |
|---|---|---|
| Content novelty of a skill | `python cli.py novelty analyze` | Estimate with `--estimate-only`; cap with `--max-usd`; output `novelty.json` |
| Re-render saved novelty | `python cli.py novelty view` | No model calls |
| Skill routing | `python cli.py novelty routing` | Free without `--model` |
| Guidance reach | `python cli.py repo reach` | Plan with `--dry-run`; cap with `--budget` |
| Context window of a run | `python cli.py context trace` | Reads an existing trace |
| Judge calibration | `python cli.py eval judge-validity` | Sequential stopping with `--sequential` |
| Tool-choice probe | `python cli.py tools preference` | Cap with `--max-cost-usd` |

**Connections.** Every other journey uses these as steps.

### Use cases and acceptance

#### UC-J14-001 Measure the novelty of a skill directly

The narrow novelty workflow: input is the skill bundle (`--artifact skill:<path>`)
and a model; the estimate comes first; the run writes `novelty.json`, and
extraction and probing can be run as separate steps (`python cli.py novelty atoms`,
`python cli.py novelty probe`) whose saved output is reused; spend is capped
per run. Its limits are those in
[the novelty limits](../skill-novelty.md#limits-to-state-when-quoting-these-numbers):
a novelty figure is a property of the pair (skill, model), it says nothing about
task outcomes, and differences inside the judge band are not quoted.

- **AC-J14-001** Every novelty subcommand that calls a model accepts a spend cap, and a capped run refuses the call after the cap is spent and stops on a call with no known price.
- **AC-J14-002** The estimate prices the bundle the run will actually send, and points at the spend cap.
- **AC-J14-003** The documented authoring path (routing, estimate, extraction, probing, shortening, view) runs end to end on stub models through the CLI and writes its intermediate artifacts.
- **AC-J14-004** Probing an inventory against an artifact that changed since extraction is refused.
- **AC-J14-008** A novelty record states the share of the skill's readable words it did not measure and the basis of that share; the gate judges a stored record on its own stamped basis, and a record with an unknown basis is reported unmeasured.
- **AC-J14-011** The novelty report shows a conflict count the gate did not clear as not measured, never as a clean 0. *Proposed.*
- **AC-J14-012** Analyze states on its output the share of readable words it measured, and warns when it is half or less. *Proposed.*

#### UC-J14-002 Inspect a run's context

- **AC-J14-005** A context trace reports per-call context size of an existing run without model calls, and does not claim downstream effects of context size. *Manual for the second half.*

#### UC-J14-003 Probe reach, routing or a judge on its own

- **AC-J14-006** Each of reach, routing and judge validity can be run alone with explicit inputs and writes structured output; reach is traced under AC-J04-003.
- **AC-J14-009** A routing run that calls a model records its calls, tokens and dollars, or no dollar figure when a call has no known price. *Proposed.*
- **AC-J14-010** Routing builds its candidate field the way the harness does (catalog manifest, hidden skills left out, each mirror once), and with repeats reports agreement per query as its noise floor. *Proposed.*

#### UC-J14-004 Re-render saved evidence

- **AC-J14-007** Saved output re-renders to a report without model calls. *Partial: novelty and repository batches.*

---

## J15 — Turn a solved problem into a skill

**Actor and intent.** Someone who just solved a problem with an agent in a chat
and wants that know-how to be reusable. "Make a skill out of what we just did."
"Is my skill better than what the standard skill-creator would write?"
Trigger: a chat or session that ended with a working solution, and no skill.

**Starting point and inputs.** The chat transcript (or a summary of it) and
the solution; no skill, no tasks, no evals. The alternative the user weighs is
the standard skill-creator (for example
[anthropics/skills skill-creator](https://github.com/anthropics/skills/tree/main/skills/skill-creator)),
or no skill at all. Questions: which models and harnesses the skill must work
for; which other problems of the same kind it should help with; the budget.

**Flow.**

1. Turn the solved problem into a task with a grader, and draft a few
   related tasks the skill should also help with. The solved problem is the
   source of the skill, so it is kept apart from the held-out tasks
   ([J7](#j7--create-or-improve-evals) for the graders and the self-check).
2. Write the skill from the transcript: by hand, by the agent, or both.
3. Check what the draft adds over what the model already knows
   (`python cli.py novelty analyze --artifact skill:<path>`). Content the model
   already knows is a reason to shorten, and a skill that adds nothing is a
   reason to stop.
4. Check the skill is selected for the requests it is for
   (`python cli.py novelty routing`).
5. Run three arms on the same tasks, model and repetitions: no skill
   (`benchflow:<agent>`), the user's skill (`benchflow-skill:<agent>:<path>`),
   and a skill the named skill-creator generates
   (`benchflow-selfgen:<agent>:<creator-dir>`), with `python cli.py tasks run`
   and `--baseline-harness` set to the no-skill arm.
6. Keep the user's skill, keep the generated one, merge them, or keep none,
   with the numbers; then continue in [J2](#j2--improve-my-skill).

**Branches.**

- *Only the source problem as a task.* A gain on it shows the skill restates
  the solution, not that it generalizes; report it apart and say so.
- *The skill adds nothing the model lacks.* Novelty is near zero: a skill may
  still help with routing or procedure, but the task arms have to show it.
- *The generated skill does as well.* That is a result: the hand-written skill
  is not worth its upkeep, or the two are merged.
- *No skill-creator available.* Compare the user's skill with no skill only,
  and say the creator comparison was not run.
- *Neither skill beats no skill.* Not shipping a skill is a valid outcome.

**Outputs.** A skill (or a decision not to make one); a small task set with
the source problem kept apart; the effect of the user's skill and of the
generated one against no skill, with their uncertainty; the novelty of the
draft on the target model.

**Measurement scope.** The task set is small and drawn from one session, so an
effect is about those tasks, that model and that harness. The generated skill
depends on the creator version and its own model; both are recorded.

**Today.** Steps 3–5 exist: novelty and routing for a skill directory, and the
three BenchFlow arms in one `tasks run`, with a named skill-creator staged
alone. Steps 1 and 2 are **proposed**: no command turns a transcript into tasks
or drafts a skill from it, and nothing marks the source task apart from the
held-out ones in a report.

**Connections.** Uses J7 for tasks, J14 for novelty and routing, J10 for the
arm comparison; continues as J2, and J12 protects the kept skill.

**Example.** A developer fixed a flaky build in a chat and asks for a skill.
They turn the fix into one task and write two related ones. Novelty shows half
of the draft is general build advice the model already gives; they cut it.
On the three tasks, their skill and the skill-creator's both solve the source
task; only theirs solves one held-out task, with an interval too wide to call.
They keep their skill, mark the result as indicative, and add tasks.

### Use cases and acceptance

#### UC-J15-001 Turn the solved problem into tasks

- **AC-J15-001** Tasks drafted from a chat transcript are presented as hypotheses with their source, for the user to confirm. *Proposed.*
- **AC-J15-002** The task built from the problem the skill was written from is reported apart from the held-out tasks, and an effect on it alone is not reported as the skill's effect. *Proposed.*

#### UC-J15-002 Decide whether a skill is needed

- **AC-J15-003** The novelty of a draft skill on the target model can be measured before any task run, and reports the parts the model already knows.

#### UC-J15-003 Compare with the standard skill-creator

- **AC-J15-004** The user's skill, a skill generated by a named skill-creator, and no skill run as arms on the same tasks, model and repetitions, and the report gives each skill's effect against no skill with its uncertainty.
- **AC-J15-005** The skill-creator arm uses exactly the creator the user names: a stock creator next to it does not win, and a named directory with no skill-creator `SKILL.md` is refused rather than replaced by the host's.
