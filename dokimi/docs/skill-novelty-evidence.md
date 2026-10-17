<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

# Evidence: a 33-skill content-novelty study

The numbers quoted in [skill-novelty.md](skill-novelty.md) come from one study, recorded here so
every claim has a method and a caveat attached.

**Subject:** `https://github.com/intel/skills` pinned at `2891d7a7d1ba7c262833a6beaf0ac533f9023c65`,
clean tree, 33 skills. The repo's own checks passed at that commit (`validate_skills.py` OK 33,
`run_evals.py --validate` OK 10 files / 30 cases), so the study measured content quality on a
structurally valid corpus, not a broken one.

**Provenance:** run with a prototype outside this repository, against a LiteLLM proxy in front of
Bedrock. Nothing in the subject repository was modified. Reimplementing that pipeline inside
`dokimi/novelty/` is what makes the measurement reproducible from this repo; until it
lands, treat the tables below as evidence for the design, not as a regression baseline.

**Model scope:** current-generation models only. Two previous-generation models were measured early
and are excluded from every table here, because "what does this artifact add" is only interesting for
models someone would deploy today.

---

## 1. Method, and the constraints it had to satisfy

| stage | what it produced |
|---|---|
| inventory | 33 skills, token counts, frontmatter, eval files |
| need graph | 13 graphs, 1076 nodes, 1536 edges — built from name + description only |
| atom extraction | 4770 knowledge atoms with span-level provenance |
| faithfulness check | 4411 supported / 329 distorted / 30 unsupported |
| value + category | 2422 atoms judged (capped at 250 per skill) |
| span map | 4796 spans, atom ↔ span index, protection flags |
| probes | 19524 graded probe runs over 10 models and 6 arms |
| shortening | 462 candidate scorings, 15 candidates × 4 skills |
| behaviour | 104 generated tasks (66 discriminating), 651 task runs |
| routing | 891 routing decisions over 3 menu seeds |

Four constraints shaped the design, and each was verified rather than assumed:

1. **The artifact must not define its own relevance.** The need graph's author never saw a skill
   body — only its name and description. 100 nodes were labelled `out-of-scope` on purpose, as
   negative controls.
2. **Never grade with the model that wrote the answer key.** Extraction, answering and grading were
   three separate models. A second grader double-scored 2143 responses.
3. **Never use self-confidence as the correctness score.** Verified as necessary: the correlation
   between stated confidence and graded score was ≤0.454 and as low as 0.018.
4. **Baseline isolation.** A baseline probe received one question and nothing else — no repository
   contents, no skill text, no answer key, no prior turns.

### Judge reliability — the caveat on every level below

2143 double-graded responses: **exact agreement on the 6-way verdict scale 0.664**, agreement on the
correct-ish binary collapse **0.852**. The largest confusion was `partially_correct` vs `wrong_other`
(247 cases). Consequently the 6-way scale is not used for any conclusion, every score appears under
both a lenient and a strict collapse, and paired comparisons are preferred over levels.

Human review sheets were generated for a gold subset (40 atoms, 40 graded responses, plus the
constructed equivalence pairs) but **have not yet been filled in**. Until they are, the absolute
level of every score rests on an LLM judge.

---

## 2. What the models already knew (K0)

Prior recall over the skills' own knowledge, no artifact in context. `reversal_resisted` is the
fraction of planted-inversion probes the model refused.

| model | K0 lenient | K0 strict | adversarial | reversal resisted | conf↔score corr |
|---|---|---|---|---|---|
| gpt-5.6-luna | 0.499 | 0.240 | 0.478 | 0.804 | 0.112 |
| kimi-k3 | 0.494 | 0.276 | 0.491 | 0.807 | 0.390 |
| claude-opus-5 | 0.472 | 0.250 | 0.440 | 0.793 | 0.257 |
| claude-sonnet-5 | 0.452 | 0.209 | 0.421 | 0.811 | 0.312 |
| nemotron-super-3-120b | 0.358 | 0.129 | 0.367 | 0.873 | 0.201 |
| grok-4.6 | 0.352 | 0.182 | 0.322 | 0.787 | 0.392 |
| claude-haiku-4-5 | 0.340 | 0.112 | 0.328 | 0.843 | 0.454 |
| deepseek-v3.2 | 0.336 | 0.082 | 0.303 | 0.872 | 0.270 |
| glm-5 | 0.331 | 0.110 | 0.300 | 0.860 | 0.386 |
| nemotron-nano-12b | 0.257 | 0.033 | 0.255 | 0.890 | 0.018 |

**An inversion worth designing around:** the strongest models resist reversal probes *least*
(0.787–0.811), the weakest most (0.890). A weak model abstains and thereby dodges a planted
inversion; a strong model engages and sometimes accepts it. A stale or reversed line in a skill is
therefore most dangerous to the best models — and the skill in this corpus with the most conflicting
content also had one of the highest routing rates, so it loads reliably and then misinforms.

## 3. What the skill added

Paired per probe; four exhaustive outcomes.

| model | n | added | already knew | degraded | still missing |
|---|---|---|---|---|---|
| **lenient grading** | | | | | |
| claude-opus-5 | 573 | 0.209 | **0.700** | 0.019 | 0.072 |
| gpt-5.6-luna | 573 | 0.131 | **0.775** | 0.030 | 0.065 |
| grok-4.6 | 573 | **0.307** | 0.537 | 0.024 | 0.131 |
| claude-haiku-4-5 | 263 | 0.232 | 0.597 | 0.049 | 0.122 |
| nemotron-nano-12b | 263 | 0.293 | 0.498 | **0.076** | 0.133 |
| **strict grading** | | | | | |
| claude-opus-5 | 573 | 0.363 | 0.216 | 0.038 | 0.382 |
| gpt-5.6-luna | 573 | 0.286 | 0.220 | 0.033 | 0.461 |
| grok-4.6 | 573 | 0.356 | 0.157 | 0.024 | 0.463 |
| claude-haiku-4-5 | 263 | 0.304 | 0.103 | 0.030 | 0.563 |
| nemotron-nano-12b | 263 | 0.266 | 0.019 | 0.027 | **0.688** |

Read the two gradings together. Under lenient grading a skill mostly repeats what the model knows;
under strict grading its real job appears — the gap between `added` lenient (0.209) and strict
(0.363) on `claude-opus-5` is exactly the class *"the model knew the shape, the skill supplied the
precision"*. **Qualifiers and exact values are what these skills are for.**

### Control arms

The `no_skill` column is §2's K0 lenient, not a rate over §3's paired cells: there
`claude-opus-5` is right without the skill on 0.700 + 0.019 = 0.719 of probes.

| model | no_skill | root (`SKILL.md` only) | routed | full | placebo | shuffled |
|---|---|---|---|---|---|---|
| claude-opus-5 | 0.472 | 0.686 | 0.728 | 0.730 | 0.418 | **0.750** |
| gpt-5.6-luna | 0.499 | 0.667 | 0.709 | 0.698 | 0.482 | **0.735** |
| grok-4.6 | 0.352 | 0.537 | 0.627 | 0.671 | **0.104** | 0.690 |
| claude-haiku-4-5 | 0.340 | 0.540 | 0.595 | 0.597 | 0.130 | **0.637** |
| nemotron-nano-12b | 0.257 | 0.464 | 0.471 | 0.505 | 0.184 | 0.483 |

- **`placebo`** is domain-mismatched text of the same length. It sits far *below* `no_skill` on
  `grok-4.6` (0.104 vs 0.352) and `claude-haiku-4-5` (0.130 vs 0.340): irrelevant context actively
  damages those models, so the `full` gain is not a prompt-length artifact. On `claude-opus-5` and
  `gpt-5.6-luna` placebo ≈ `no_skill` — they ignore irrelevant context instead of being hurt by it.
- **`shuffled`** keeps the same spans and destroys their order. It scores *at least as high* as
  `full` on four of five models. Ordering and narrative are not carrying knowledge in this corpus.
  A probe cannot punish a scrambled document, so this was retested behaviourally on the tasks whose
  graded requirements name an ordering: the two arms stay level there too (§10.12).

## 4. Content categories

2422 judged atoms, primary lens `claude-opus-5`:

| category | n | share |
|---|---|---|
| scaffolding | 947 | 39.1% |
| novel_useful | 718 | 29.6% |
| known_useful | 331 | 13.7% |
| novel_inert | 257 | 10.6% |
| harmful_conflicting | 97 | 4.0% |
| known_inert | 72 | 3.0% |

**Uncertainty:** an independent reviewer (853 atoms) agreed on 71.6%. The disagreements are
systematic, not noise — `novel_useful → novel_inert` 70, `known_useful → novel_useful` 48,
`known_useful → novel_inert` 27, `novel_useful → scaffolding` 26. Both axes that slip are the judged
ones. Treat useful-vs-inert as ±3 points and known-vs-novel as ±2; the scaffolding share is the most
robust number in the table, and it is also the largest.

88 atoms were flagged as internally contradictory or contradicting a sibling skill, concentrated in
one skill (30 of 88).

## 5. Per-skill results

Judged atoms (250/skill cap) / spans / tokens / share of tokens carrying no atom / novel_useful /
scaffolding / harmful / inert / routing accuracy:

> **Correction — read the `dead tok` column with §10.7 open.** That column counts spans carrying no
> *judged* atom, and judging was capped at 250 atoms per skill. On the two skills where the cap bit
> hardest the column therefore measures the cap, not the text: recomputed against the full extracted
> atom set, `performance-patterns` is **6.4%** dead and not 74%, and `linux-perf` is **6.8%** and not
> 50%. The column is correct only where the cap did not bind (`dpnp-quickstart`, 83 of 83 atoms
> judged, 13.1% either way). §10.7 has the measurement and the consequences.

| skill | atoms | spans | tokens | dead tok | nov_useful | scaffold | harmful | inert | routing |
|---|---|---|---|---|---|---|---|---|---|
| performance-patterns | 279 | 2087 | 59248 | **74%** | 42 | 82 | 11 | 64 | 0.771 |
| linux-perf | 271 | 1042 | 28421 | **50%** | 28 | 108 | 5 | 89 | 0.583 |
| model-config-recommend | 262 | 203 | 24501 | 12% | 138 | 80 | 5 | 12 | 0.381 |
| xpu-system-setup | 259 | 159 | 17371 | 6% | 77 | 120 | 11 | 30 | 0.333 |
| mkl-extension-advisor | 259 | 316 | 9421 | 17% | 76 | 108 | **33** | 20 | 0.810 |
| model-can-it-fit | 252 | 227 | 14267 | 7% | 123 | 79 | 8 | 6 | 0.813 |
| vllm-xpu-run | 224 | 203 | 8304 | 5% | 82 | 88 | 12 | 25 | 0.292 |
| xpu-runtime-preflight | 145 | 72 | 6617 | 2% | 60 | 36 | 4 | 37 | 0.604 |
| xpu-deploy-plan | 139 | 87 | 6280 | 4% | 28 | 78 | 2 | 29 | **0.146** |
| vllm-xpu-bench | 104 | 152 | 4107 | 9% | 28 | 51 | 0 | 6 | 0.625 |
| dpnp-troubleshooting | 84 | 86 | 2445 | 12% | 28 | 37 | 4 | 6 | 0.438 |
| dpnp-quickstart | 83 | 95 | 2351 | 13% | 21 | 53 | 2 | 1 | 0.292 |
| onetbb-quickstart | 61 | 67 | 2049 | 14% | 12 | 27 | 0 | 4 | 0.813 |

Four distinct failure modes appear, and they need different fixes:

- **Bulk without knowledge** — `performance-patterns` and `linux-perf` hold the two largest inert
  counts (64 and 89 inert atoms) and are the two largest artifacts. *Their `dead tok` figures do not
  support this label* (see the correction above and §10.7); the inert counts and the size do. Shorten
  on the inert-atom evidence, not on dead tokens.
- **Correctness risk** — `mkl-extension-advisor`: 33 conflicting atoms in 9.4k tokens, and it routes
  at 0.810, so it *will* be loaded. Fix content before shortening.
- **Routing failure** — six skills below 0.50, `xpu-deploy-plan` at 0.146. Their descriptions do not
  claim their own queries; content work there is unrecoverable spend.
- **Clean but inert** — `xpu-runtime-preflight`: 2% dead tokens and 60 novel_useful atoms, but 37
  inert atoms and middling routing.

## 6. Shortening, with behaviour measured rather than estimated

Candidate packages were rendered and then **actually run** through the generated task suite, so the
retention oracle's estimate can be checked against behaviour:

| skill | candidate | tok frac | knowledge | protection | measured Δ task vs full |
|---|---|---|---|---|---|
| performance-patterns | safety-pinned 0.75 | 0.66 | 0.957 | 1.000 | opus +0.08, luna −0.01, grok +0.05 |
| performance-patterns | safety-pinned 0.50 | 0.60 | 0.812 | 1.000 | opus +0.14, luna −0.00, grok −0.08 |
| performance-patterns | **value-only 0.50** | **0.21** | 0.916 | **0.158** | opus +0.05, luna −0.07, grok +0.16 |
| mkl-extension-advisor | **value-only 0.50** | 0.50 | 0.844 | **0.625** | haiku +0.06, opus **+0.21**, nano −0.07 |
| dpnp-quickstart | safety-pinned 0.50 | 0.59 | 0.783 | 1.000 | haiku +0.13, opus +0.05, nano **−0.19** |
| xpu-system-setup | safety-pinned 0.75 | **0.94** | 0.967 | 1.000 | opus 0.00, haiku **+0.30**, nano +0.06 |

Before reading the `value`-named rows: §10.10 measures that per-model `V` was defined on only 3–14
atoms per artifact here, so those candidates were selected by atom coverage per token far more than by
relevance. The behavioural results below stand as measured; the label on the strategy overclaims.

1. **No shortened candidate lost more than 0.08 against the full package on a strong model.** Every
   task-score loss beyond −0.08 belonged to the smallest model (−0.10, −0.15, −0.19).
2. **The knowledge oracle cannot see the safety loss.** The 21%-token candidate reports knowledge
   0.916 and protection 0.158. Negations, version gates and ordering constraints are exactly the
   low-value-per-token spans a greedy selector discards, because their value is conditional on a
   failure the atom statement does not encode. This is why the five axes stay separate.
3. **Some artifacts cannot be shortened.** `xpu-system-setup` under any safety-pinned strategy lands
   at 0.94 token fraction: nearly every span is protected or a sole carrier. Its problem is content,
   not length.
4. **Deduplication found nothing.** A dedupe-only pass on `performance-patterns` returned token
   fraction 1.01 — there is no near-duplicate knowledge to merge. The original reading of this result
   ("its 74% dead tokens are not duplicated knowledge") does not survive §10.7: at 6.4% dead tokens
   there was little dead text to explain in the first place. What stands is the narrow claim:
   dedupe is not a shortening strategy for this artifact.

## 7. Behaviour and routing

Generated suite: 104 tasks, 102 usable, 66 discriminating, balanced across correctness (22),
debugging (21), integration (21), selection (19), performance (19). 651 graded runs, 0 failures.

| skill | model | no_skill | root | full | best shortened |
|---|---|---|---|---|---|
| performance-patterns | claude-opus-5 | 0.272 | 0.431 | 0.459 | **0.600** |
| performance-patterns | gpt-5.6-luna | 0.406 | 0.459 | 0.416 | 0.428 |
| performance-patterns | grok-4.6 | **0.537** | 0.047 | 0.397 | 0.553 |
| dpnp-quickstart | claude-opus-5 | 0.344 | **0.591** | 0.400 | 0.581 |
| dpnp-quickstart | nemotron-nano-12b | 0.078 | 0.334 | 0.281 | 0.281 |

**The 0.047 cell is a content defect, not a harness bug.** `grok-4.6` abstained on 6 of 8 tasks when
given that `SKILL.md` alone, answering with a deferral instead of an answer — verbatim: *"I'll look
up the performance-pattern guidance for `-Ofast` vs. numerical correctness, then give you the exact
flag split. I'll start with the write-time and source-review checklists."* The grader recorded
`abstained: true` with all five requirements unmet. The skill's progressive-disclosure phrasing
teaches the model to postpone answering; with no skill at all the same model scored 0.537. Every
other model/arm cell had ≤2 abstentions, except `claude-opus-5 / no_skill` (12/31), which is honest
ignorance.

**Routing.** All 33 real descriptions as a menu; the correct skill must be chosen per task.
Accuracy: 166/306, 168/306, 160/306 across three menu-order seeds — a ±1.3-point sensitivity,
measured rather than assumed. Shortened candidates are routing-invariant by construction
(frontmatter is pinned) and measured as such. **Shortening does not threaten routing; the
descriptions were already the weak part.**

## 8. Which matcher can decide "is this the same knowledge?"

Two places in the pipeline ask a comparison question, and they are not the same question:

| where | question | wrong answer costs |
|---|---|---|
| retention oracle | is atom `A` still recoverable from shortened text `T`? | a shortening is accepted that silently dropped a rule |
| atom ↔ need match | does atom `A` answer need node `N`? | relevance, and therefore `value`, is assigned to the wrong atoms |

Both were benchmarked on the same 714 constructed pairs (restates / entails / unrelated / reverses),
construction-blind agreement 0.961:

| approach | macro-F1 | LLM calls | contradiction recall |
|---|---|---|---|
| llm_direct | 0.953 | 100% | high |
| hybrid (retrieval → NLI → LLM band) | 0.848 | 25.6% | high |
| nli | 0.732 | 0% | moderate |
| graph matcher | 0.551 | 0% | — |
| lexical | 0.446 | 0% | **0.00** |
| embeddings (potion / bge) | 0.444 / 0.438 | 0% | **0.00** |

Three conclusions, in order of how much they changed the design:

1. **Embedding and lexical matchers cannot distinguish *restates* from *reverses*.** Contradiction
   recall 0.00 for both. A purely vector-based content tool would score a reversed claim as
   faithfully retained — the exact failure the layer exists to catch.
2. **The hybrid is the shipped oracle**: retrieval prefilter → two-way NLI → LLM adjudication only
   in the ambiguous band. It keeps high contradiction recall at 25.6% of the LLM calls of direct
   judging, and it degrades in the safe direction — without an adjudicator the band resolves to
   *absent*, so reported `knowledge` is a floor, never an overstatement.
3. **The graph matcher is the second-worst scorer here (0.551) and that is not an argument against
   the graph.** Structural overlap is the wrong instrument for semantic equivalence. What the graph
   is good at is the previous step — deciding *which question is being asked at all*, see §9.

## 9. The need graph: what it decides, what it cannot, and how it compares

The graph exists to satisfy one constraint: **an artifact must not define both the problem scope and
its own relevance to that scope.** Its author saw a skill's `name` and `description` and nothing
else — never the body, never the atoms, never an eval file.

**Measured build.** 13 graphs, 1076 nodes, 1536 edges.

| node type | n | path class | n | weight used in `value` |
|---|---|---|---|---|
| intent | 325 | rare_critical | 356 | 1.0 |
| evidence | 220 | core | 311 | 1.0 |
| action | 198 | branch | 275 | 0.7 |
| constraint | 168 | supporting | 34 | 0.4 |
| decision | 165 | out_of_scope | 100 | 0.0 |

`rare_critical` carries the same weight as `core` deliberately. A rule that fires on one query in a
hundred but prevents a corrupted run is not low-value; frequency and criticality are different axes,
and collapsing them is how safety text gets deleted. The 100 `out_of_scope` nodes were planted as
negative controls: needs a reader might plausibly bring that this artifact is *not* responsible for.

Relevance of an atom is then a sum over the needs it serves, rather than a similarity score:

```
R(a) = Σ_t  P(t) · Need(a,t) · Criticality(a,t) · Uniqueness(a,t)
```

`P(t)` — how often the need actually arises — is **not measured** in this study. Descriptions,
issues and support queries would supply it; absent that, every `value` number here is reported under
uniform `P(t)`, and the honest use is ranking within one artifact, not comparing artifacts.

### 9.1 Graph-derived relevance vs. plain similarity

If the graph were decorative, relevance derived from it would track embedding similarity, and the
cheaper instrument would win. It does not track it:

| comparison | result | reading |
|---|---|---|
| corr(path-weighted relevance, top retrieval similarity) | **0.368** | the two rankings measure different things |
| top-quartile overlap | 261/605 | they disagree on **57%** of the atoms each calls most relevant |
| atoms whose best match is `out_of_scope` | **32 (1.3%)** | a category similarity cannot express at all |
| author ↔ independent critic on path labels | 925/1076 (**86%**) | the 14% is the noise floor on relevance-weighted `value` |

The 1.3% is the point, not the size. Similarity ranking answers *how close is this text to the
topic*; it has no way to return "this sentence answers a question this artifact is not responsible
for". Being on-topic and being in-scope are different properties, and only the second one licenses
spending tokens.

### 9.2 Edges do the work that node similarity cannot

Atoms are not only propositions. The most consequential content in this corpus was **negative
edges** — short clauses that block an inference a model would otherwise make. Five examples found in
the static audit, each one a single clause whose deletion flips an edge from
`evidence IMPLIES conclusion` to `evidence DOES_NOT_IMPLY conclusion`:

| clause (paraphrased) | edge it blocks |
|---|---|
| a successful `import` does not mean the array landed on the GPU | import success → device placement |
| `threadpool_info()` seeing MKL does not mean NumPy's BLAS/FFT are wired to it | library visible → library in use |
| bare device discovery reports no memory figure | device listed → capacity known |
| FITS against physical VRAM does not mean it will launch now | static fit → runtime launch |
| do not chain the child skills by hand, use the single entrypoint | sub-skill exists → call it directly |

Each is cheap in tokens, invisible to a frequency or perplexity ranking, and expensive to lose. This
is why `protection` is a separate axis (§6) and why the vocabulary the graph needs is **qualified**
edges — `only_if`, `not_evidence_for`, `before`, `fallback_when`, `halts_on` — not a single
`related_to`. The qualified-edge vocabulary is design, taken from the audit; the node and path counts
above are measured.

### 9.3 Five things the graph is not allowed to decide

The graph organizes the question space. It is a retrieval and exploration layer — the role GraphRAG
plays — and explicitly not the scorer:

| the graph cannot say | what actually answers it |
|---|---|
| whether a claim is **true** | external primary sources; not done here (§11.2) |
| how **often** a need arises | intent telemetry, issues, support queries — the missing `P(t)` |
| what **this model** already knows | paired probes per (atom, model) — §2, §3 |
| which span **caused** a behaviour change | deletion / leave-one-out arms — §6 |
| whether the knowledge is **useful downstream** | the task suite — §7 |

Every one of those is a separate measurement in this study, which is why the headline output is a
dashboard and not a single graph score. A run that reported only graph coverage would have called
`performance-patterns` healthy and missed the deferral defect that cost
`grok-4.6` 0.49 of task score against no skill at all.

## 10. Follow-up experiments on the same data

The sections above are the original sweep. §10.1–§10.6, §10.8, §10.10 and §10.15 re-cut the *same*
graded probes and task runs to answer questions the headline tables raise but cannot settle. Because
they add no new model calls, they add no new judge noise — they can only redistribute the noise
already present. §10.7, §10.9 and §10.11–§10.14 make new model calls (extraction, relabelling, the
retention oracle, graded task runs) and carry their own noise.

The `O`-numbers from here on are this layer's working labels for its open questions; each
heading states its question in words, and the label only ties a subsection to its follow-ups and to
the `dokimi/novelty/` comments that cite it.

Two definitional differences apply to every table in §10 and must not be read across §2/§3:

- **`K0` here is a per-cell rate, not a mean score.** It is the fraction of baseline probes whose
  verdict falls inside the collapse set. §2's `K0` is the mean graded `score` over all baseline
  probes. On `claude-opus-5` the two read 0.802 and 0.472 respectively; neither is wrong, they are
  different statistics.
- **Planted inversions are excluded from the 2×2 here.** §3 pools them in, so `added` is lower in
  §10.1 (opus 0.156 lenient) than in §3 (0.209) on the same model. For an inversion probe a *low*
  score is the correct outcome, so it does not belong in a "did the artifact supply this" count.

### 10.1 Which kind of knowledge a model lacks (open question O8)

The headline 2×2 averages over everything an artifact says. Re-grouped by what the atom *is* —
tags assigned at extraction, multi-label, so **rows do not partition the set and must never be
summed** — the average splits cleanly. Strict grading, `full` vs `no_skill`:

| model | public: K0 / added | local: K0 / added | procedural: K0 / added | negative-constraint: K0 / added |
|---|---|---|---|---|
| claude-opus-5 | 0.545 / 0.222 | **0.235 / 0.426** | 0.200 / 0.463 | 0.382 / 0.408 |
| gpt-5.6-luna | 0.532 / 0.170 | 0.259 / 0.333 | 0.214 / 0.429 | 0.366 / 0.254 |
| grok-4.6 | 0.406 / 0.229 | 0.160 / 0.352 | 0.156 / 0.400 | 0.189 / 0.322 |
| claude-haiku-4-5 | 0.371 / 0.171 | 0.132 / 0.329 | 0.125 / 0.325 | 0.167 / 0.278 |
| nemotron-nano-12b | 0.227 / 0.227 | **0.000** / 0.321 | 0.063 / 0.281 | 0.046 / 0.273 |

Paired cells per model: 283 / 272 / 310 / 116 / 81. Tag cells of n<25 are marked thin in the
artifact and are not quoted here (`version-bound` is n=3–15 everywhere, and `rare-critical` is thin
below opus/luna/grok). §10.9 re-derives this table under two other labellers on the same probes: the
`public`-vs-`local` direction and magnitude survive, while the `type` rows below rest on the least
reproducible part of the vocabulary.

By atom shape, same models, strict `added`:

| type | n (opus) | opus K0 / added | grok K0 / added |
|---|---|---|---|
| proposition | 126 | 0.437 / 0.270 | 0.314 / 0.300 |
| edge | 64 | 0.375 / 0.406 | 0.203 / 0.406 |
| contract | 54 | **0.167** / 0.426 | 0.150 / 0.300 |
| motif | 36 | **0.167** / **0.556** | 0.100 / 0.375 |

Three readings, and they are the most directly actionable result in the study:

1. **Models know the concepts and not the contract.** On every model, prior knowledge of `public`
   atoms is roughly double that of `local` ones, and the artifact's contribution runs the other way.
   A skill whose body is mostly public conceptual material is spending tokens on the half the model
   already has.
2. **`motif` and `contract` are where an artifact is least replaceable** — ordered procedures, gates,
   flags, output formats. `proposition` is where it is most replaceable (K0 0.437 on opus, the
   highest of any shape). This is the same finding as the lenient/strict gap in §3, localized to a
   content type an author can act on.
3. **The weakest model knows *no* local contract at all** (`nemotron-nano-12b`, strict K0 0.000 over
   53 cells) yet the artifact only lifts it to 0.321. Supplying the fact is not the same as the model
   being able to use it; that is a behaviour question, not a knowledge one (§7).

### 10.2 Loading the artifact makes reversal resistance *worse*, paired

§2 noted that strong models resist planted inversions least. The arm means could not settle whether
the artifact itself changes resistance, because the arms have different `n`. Pairing each inversion
probe with itself across arms and sign-testing the change:

| model | n paired | mean Δ resistance (`full` − `no_skill`) | worse / better / unchanged | sign p |
|---|---|---|---|---|
| claude-opus-5 | 26 | −0.092 | 8 / 2 / 16 | 0.109 |
| gpt-5.6-luna | 47 | −0.060 | 12 / 5 / 30 | 0.143 |
| grok-4.6 | 13 | −0.162 | 5 / 1 / 7 | 0.219 |
| claude-haiku-4-5 | 23 | −0.087 | 6 / 1 / 16 | 0.125 |
| nemotron-nano-12b | 53 | −0.068 | 14 / 5 / 34 | 0.064 |
| **pooled** | 162 | — | **45 / 14 / 103** | **7e-05** |

No single model reaches significance; **all five point the same way**, and the pooled sign test is
decisive. The pooled `p` assumes probes are independent across models, which they are not — the same
planted inversions are reused — so read the *direction and the consistency*, not the p-value.

The mechanism is not mysterious: an in-context artifact raises the model's willingness to commit,
and a reversed premise in the question gets committed to along with everything else. **Anything
loaded into context is a lever on confidence, so content correctness must be gated before content
volume.** It also sharpens §5: `mkl-extension-advisor` has 33 conflicting atoms and routes at 0.810.

### 10.3 How many probes are enough (open question O4) — the answer is "more, not fewer"

This experiment was intended to find a cheap configuration. Subsampling the paired cells per skill
at 200 seeds per budget and comparing the resulting 13-skill ranking against the full-data ranking:

| budget | rho (opus / luna / grok), lenient | 5th pct rho | gate flip rate at `added ≥ 0.15` | top-3 overlap |
|---|---|---|---|---|
| 0.75 | 0.909 / 0.914 / 0.954 | 0.70–0.91 | 0.03–0.11 | 0.77–0.82 |
| 0.50 | 0.789 / 0.760 / 0.890 | 0.47–0.79 | 0.02–0.20 | 0.64–0.78 |
| 0.25 | 0.579 / 0.619 / 0.752 | 0.20–0.56 | 0.07–0.24 | 0.46–0.66 |
| 0.10 | 0.435 / 0.543 / 0.629 | −0.03–0.32 | 0.08–0.33 | 0.38–0.68 |

**Below 0.75 the artifact ranking stops being stable, and at 0.25 a gate at `added ≥ 0.15` changes
its verdict on up to 24% of skills.** The cause is visible in the inputs: paired cells per skill are
only **12–29** (median 20–24). The full sweep is already near the floor, which is why the per-skill
table in §5 is reported as a profile rather than a ranking, and why the soft-gate thresholds
cannot harden on this data. The measurement to make cheap is the *number of models*, not the number
of probes per skill.

### 10.4 At what granularity a "this topic is redundant" verdict is reportable (open question O7)

Granularity was swept over the heading path each atom was extracted under — not a clustering — so
every group is something an author can actually delete. A topic verdict is only honest if the atoms
inside it agree:

| level | topics | atoms/topic | unanimous (lenient) | unanimous (strict) | majority share (strict) |
|---|---|---|---|---|---|
| whole artifact | 13 | 12.6–13.9 | **0.00–0.31** | **0.00** | 0.48–0.58 |
| file | 45–47 | 3.6–3.9 | 0.45–0.76 | 0.36–0.39 | 0.70–0.73 |
| heading | 49–51 | 3.3–3.6 | 0.51–0.80 | 0.39–0.44 | 0.72–0.75 |
| single span | 137–148 | ~1.2 | 0.93–0.97 | 0.91–0.95 | 0.96–0.99 |

- **No artifact is ever unanimous under strict grading. Not one, on any model.** "This skill is
  redundant" is not a statement this instrument can make; the finest defensible unit that is not a
  single span is a heading, and even there strict unanimity is only 0.39–0.44.
- **Topic verdicts must therefore ship as distributions**, e.g. "7 redundant / 2 added / 1 degraded",
  never as a label. The `majority_outcome_share` column is the honest headline for a heading-level
  verdict: ~0.73 strict means roughly one atom in four disagrees with its own section's label.
- **The depth sweep collapsed.** Heading paths in this corpus as recorded are one level deep, so
  depth-1, depth-2 and depth-3 are identical and barely differ from file level. Nesting granularity
  is untested here — an artifact with real heading hierarchy would be needed.

### 10.5 Where the judge is unreliable

§1's global 0.852 / κ 0.663 hides a systematic pattern. Sliced over the same 2143 double-graded
responses (binary collapse, Cohen's κ):

| slice | n | binary agreement | κ |
|---|---|---|---|
| **by atom shape** | | | |
| motif | 266 | 0.823 | **0.558** |
| edge | 432 | 0.836 | 0.605 |
| contract | 404 | 0.837 | 0.648 |
| proposition | 991 | 0.871 | **0.714** |
| **by tag** | | | |
| rare-critical | 252 | 0.837 | **0.595** |
| routing | 430 | 0.819 | 0.598 |
| version-bound | 92 | 0.804 | 0.615 |
| public | 627 | 0.888 | **0.717** |
| **by arm** | | | |
| no_skill | 777 | **0.752** | **0.517** |
| full | 265 | 0.928 | 0.719 |
| routed | 259 | 0.919 | 0.753 |

Two consequences that constrain how §10.1 may be read:

1. **The judge is least reliable on exactly the content types the artifact contributes most** —
   `motif` (κ 0.558) and `edge` (0.605) versus `proposition` (0.714). §10.1's "motifs are where the
   artifact is least replaceable" therefore carries a wider error bar than its `n` suggests. The two
   effects are independent, not circular: an unreliable judge on motifs adds noise in both arms, and
   noise shrinks a paired difference rather than manufacturing one. The direction stands; the
   magnitude is soft.
2. **Baseline answers are the hardest thing in the study to grade** (κ 0.517, agreement 0.752, versus
   0.92 with an artifact loaded). A model answering from prior knowledge alone hedges, and hedged
   text is where graders split. Every `K0` in this study is the noisiest number on its row, which is
   a further reason paired outcomes are preferred over levels.

### 10.6 Is a shortened package actually *not worse*? (C2/C3)

§6 reports per-cell task-score differences and leaves the reader to judge them. "Not worse" is a
non-inferiority claim, so it needs a margin and an interval. Paired by task within each
(skill, model), 4000-draw paired bootstrap, margin **0.05** task-score points:

| scope | verdict counts |
|---|---|
| per (skill, model, candidate), 60 cells, vs `full` | 13 non-inferior / **44 inconclusive** / 3 worse |
| the same cells vs `no_skill` | 13 better than no artifact / 46 inconclusive / 1 not better |

**Median 95% CI width is 0.32 task-score points, on 7–8 tasks per cell.** Almost every per-cell
number in §6 — including `opus +0.21` and `nano −0.19` — is inside its own interval. Those cells
are directional hints, not measured effects, and §6 should be read that way.

Pooling every (skill, model, task) observation per candidate buys the power back, at the cost of
assuming the skills are exchangeable (they are not, so this is an average over the sweep and never a
per-skill licence):

| candidate | n obs | mean Δ vs full | 95% CI | worse/better | sign p |
|---|---|---|---|---|---|
| value-only 0.75 (safety-pinned) | 93 | +0.055 | **[+0.005, +0.106]** | 28 / 45 | 0.060 |
| value-only 0.50 | 93 | +0.042 | [−0.012, +0.095] | 29 / 33 | 0.704 |
| safety-pinned 0.50 | 93 | +0.016 | [−0.041, +0.073] | 33 / 39 | 0.556 |
| drop-inert 0.60 | 93 | +0.005 | [−0.045, +0.057] | 37 / 32 | 0.630 |
| `root` (`SKILL.md` only) | 93 | +0.012 | [−0.050, +0.072] | 30 / 38 | 0.396 |

Every shortener clears the −0.05 margin on pooled data, and none is significantly *better* except
marginally the 0.75 safety-pinned one. **Shortening these artifacts is defensible on average and
unproven individually** — which is the opposite emphasis from §6's cell-by-cell table.

**Cross-tier transfer (C3) is the real finding.** Of 20 (skill, candidate) pairs measured on 3 models
each, only **9 produce the same verdict across models**; mean spread of the per-model Δ is **0.216**,
and the worst-affected model is `nemotron-nano-12b` in 9 of 20. Three concrete inversions:

| skill / candidate | opus | haiku | nano |
|---|---|---|---|
| xpu-system-setup / value-only 0.50 | non-inferior | inconclusive | **worse** |
| dpnp-quickstart / drop-inert 0.60 | inconclusive | inconclusive | **worse** |
| performance-patterns / `root` | inconclusive | — (luna: non-inferior) | — (grok: **worse**, −0.35) |

A package validated on a frontier model is not validated for a small one. **Shortening decisions must
name the model tier they were measured on**, and a shortened artifact shipped to a mixed fleet needs
the weakest deployed tier in the loop.

### 10.7 Extractor recall, and the defect it uncovered (open question O1)

§11.5 called single-lens extraction the largest open hole. A second extractor (`gpt-5.6-luna`) was run
over five artifacts — the three smallest plus the two largest, chosen because the two largest carried
the study's most dramatic `dead tok` figures — and compared to the reference extraction
(`claude-opus-5`). 3075 reference atoms vs 3555 new ones. A third extractor (`grok-4.6`) was then run
over the three artifacts it could cover, reported separately in point 3 because it is the only lens
with no exposure to the reference set. The primary measure is mechanical: which spans did each
extractor cite at all?

| artifact | spans | tokens | ref dead | 2nd dead | union dead | ref blind spot | span Jaccard |
|---|---|---|---|---|---|---|---|
| performance-patterns | 2087 | 59248 | 0.064 | 0.102 | 0.049 | 1.6% | 0.888 |
| linux-perf | 1042 | 28421 | 0.068 | 0.091 | 0.046 | 2.4% | 0.871 |
| xpu-system-setup | 159 | 17371 | 0.017 | 0.024 | 0.014 | 0.3% | 0.896 |
| mkl-extension-advisor | 316 | 9421 | 0.052 | 0.052 | 0.049 | 0.3% | 0.963 |
| dpnp-quickstart | 95 | 2351 | 0.131 | 0.142 | 0.114 | 1.9% | 0.904 |

("blind spot" = tokens in spans that *some* extractor read as carrying knowledge and this one walked
past, as a share of the union's cited tokens.)

**1. Span coverage is reproducible; the "which text carries knowledge" question is settled.** Jaccard
0.87–0.96, and the reference extractor's blind spot is ≤2.4% of union tokens. Text the reference
called dead that the second extractor cited is 0.24%–2.25% of each artifact. Adding a second lens
moves `frac_carrying_nothing` by fractions of a point, not by a category. O1's worry — that dead
tokens are really knowledge one model could not formalize — **is not what happens here.**

**2. It uncovered a much larger defect in §5 instead.** Recomputing the dead-token share against the
*full* extracted atom set does not reproduce §5's column at all:

| artifact | judged / extracted atoms | §5 `dead tok` (judged atoms) | dead tok (all extracted atoms) |
|---|---|---|---|
| performance-patterns | 279 / 1524 | **0.743** | **0.064** |
| linux-perf | 271 / 782 | **0.504** | **0.068** |
| mkl-extension-advisor | 259 / 331 | 0.168 | 0.052 |
| xpu-system-setup | 259 / 355 | 0.057 | 0.017 |
| dpnp-quickstart | 83 / 83 | 0.131 | 0.131 |

The span map is built from the *judged* atoms, and judging was capped at 250 per skill. Where the cap
did not bind the two columns agree exactly (`dpnp-quickstart`); where it bound hardest the gap is a
factor of ten. **`performance-patterns` was never 74% dead text — it was 82% unjudged.** The two
artifacts the study singled out as "bulk without knowledge" are simply the two largest artifacts,
which is exactly what a per-skill cap turns into an apparent defect. Consequences:

- §5's failure-mode label for those two artifacts is withdrawn; its inert-atom counts and its
  routing, conflict and category columns are unaffected, because those are computed on the judged
  sample by design and reported as such.
- §6's dedupe reading is narrowed to "dedupe is not a shortening strategy here" (see §6.4).
- The engine must not derive `frac_carrying_nothing` from a capped sample. Either map spans from the
  full atom set, or report the metric together with its judged/extracted ratio so a reader can see
  the cap. **A sampling cap that is honest for one metric silently corrupts another**, and nothing in
  the original pipeline flagged the join.

**3. A third lens, fully independent, reproduces the coverage result.** `gpt-5.6-luna` had verified
the reference atom set, so it is not independent of which reference atoms were *accepted*. `grok-4.6`
was run on the three artifacts it could cover with no exposure to the reference set at all, and agrees
with the reference more closely than luna does:

| artifact | ref dead | luna dead | grok dead | union dead (3) | ref blind spot | Jaccard ref↔grok | Jaccard luna↔grok |
|---|---|---|---|---|---|---|---|
| xpu-system-setup | 0.017 | 0.024 | 0.024 | 0.014 | 0.3% | 0.921 | 0.899 |
| mkl-extension-advisor | 0.052 | 0.052 | 0.052 | 0.048 | 0.5% | 0.981 | 0.960 |
| dpnp-quickstart | 0.131 | 0.142 | 0.114 | 0.114 | 1.9% | 0.986 | 0.918 |

Three extractors, 769 / 912 / 899 atoms, and the union's dead-token share is 0.3–1.7 points below the
reference's on every artifact. Text the reference called dead and *anyone* cited is ≤1.7% of tokens.
Coverage is not a single-lens artifact.

**4. What is *not* reproducible is granularity and labelling.** The other extractors produced 17–19%
more atoms with statements ~30% shorter (21.2 and 22.8 vs 30.5 tokens mean on the three-way set), and
their tag rates diverge far more than their coverage. Shares of each extractor's own atoms, three-way:

| tag | reference | luna | grok | max ratio |
|---|---|---|---|---|
| local | 0.780 | 0.454 | 0.918 | **2.02** |
| public | 0.166 | 0.079 | 0.175 | **2.21** |
| evidence | 0.280 | 0.407 | 0.248 | 1.64 |
| rare-critical | 0.156 | 0.109 | 0.178 | 1.64 |
| procedural | 0.303 | 0.412 | 0.420 | 1.39 |
| negative-constraint | 0.225 | 0.209 | 0.263 | 1.25 |
| routing | 0.161 | 0.197 | 0.197 | 1.22 |
| temporal | 0.140 | 0.132 | 0.133 | **1.07** |

Type labels split the same way: `motif` 2.53×, `contract` 1.85×, `proposition` 1.46×. Two things fall
out of the ordering. The tags that carry §10.1's headline — `local` and `public` — are the *two least
reproducible labels measured*. The tags that carry the `protection` axis — `negative-constraint`,
`temporal`, `routing` — are the three most reproducible. On the five-artifact two-way comparison
`version-bound` diverged 2.48×, so protection is stable in aggregate and not label by label.

luna also emitted 44 atoms with off-schema `type` values (`procedural`, `procedure`, `instruction`,
`negative-constraint` used as types), which a schema check should reject rather than absorb.

This is the real limit on §10.1: **the public/local axis is one extractor's opinion.** Three
extractors agree on where knowledge is and disagree by up to 2.5× on what to call it. `local` and
`public` are the two most divergent labels of all, and they carry §10.1's headline. The *direction*
of that finding is safe — the ratio between the two tags moves the same way under both labellings,
and probes were graded against atom statements rather than tags — but any absolute per-tag rate is
extractor-conditional and must be published with the extractor named. Making the tag vocabulary
reproducible (rubric with examples, or two-extractor agreement as a reported number) is the follow-up
this creates, and it is a prerequisite for a per-tag *gate*.

**5. Statement-level recall does not reproduce either, and it is asymmetric.** The span measures ask
"did you read this text?". This one asks the stricter question the rest of the study depends on: is
*this statement* recoverable from the other extractor's statements for the same section? Run through
the same retention oracle the compression sweep uses, 120 atoms per artifact per direction, 1134
comparisons, `partial` counting as a miss:

| direction | dpnp | linux-perf | mkl | perf-patterns | xpu | pooled |
|---|---|---|---|---|---|---|
| reference atom recoverable from luna's | 0.675 | 0.642 | 0.683 | 0.642 | 0.517 | **0.629** |
| luna atom recoverable from the reference's | 0.883 | 0.808 | 0.915 | 0.925 | 0.733 | **0.851** |
| reference atom recoverable from grok's | 0.743 | — | 0.800 | — | 0.658 | **0.733** |
| grok atom recoverable from the reference's | 0.914 | — | 0.967 | — | 0.750 | **0.876** |

The third extractor — the one with no exposure to the reference atom set — reproduces the asymmetry
with the same sign and a similar gap (0.733 against 0.876 over 659 comparisons on three artifacts), so
this is a property of statement granularity and not of luna's particular relationship to the reference
set.

So 15% of luna's statements are not recoverable from the reference set, and **37% of the reference's
are not recoverable from luna's** — on artifacts where the two cited the same spans. The asymmetry
points at granularity rather than blindness: reference statements average ~31 tokens against luna's
22.3 and grok's 22.8, and a longer compound statement is harder to recover exactly from a set of shorter ones than the
reverse. Both numbers are upper bounds on knowledge lost, because the oracle's `partial` — same topic,
weaker statement — is counted as a miss here.

What this does and does not license. It does **not** reopen the dead-token question: coverage is
settled by points 1–3, and text nobody cited is still text nobody cited. It does say the atom set is
not a canonical inventory of an artifact's knowledge at statement granularity, so a per-atom count is
an extractor-conditional quantity, which is one more reason `added` is a profile of an artifact rather
than a number to rank artifacts by (§10.3, §10.8). An `added` *rate* is far more robust than an atom
count, because it is a ratio over whatever inventory the extractor produced.

### 10.8 Does `added` predict the arm delta at all? (open question O2)

The load-bearing question for the layer: if `added` does not track the task-score delta the framework
already reports, novelty describes an artifact without telling an author anything the cheap number
does not. 12 (artifact, model) cells have both a paired probe set (14–27 probes) and a paired task
set (7–8 tasks); Spearman, three ways, because a pooled correlation confounds model strength with
artifact quality.

| what is being ranked | strict `added` | strict `K0` | strict `degraded` | groups |
|---|---|---|---|---|
| pooled over all 12 cells | **0.620** | −0.632 | −0.720 | 1 |
| pooled over the 4 cells whose delta excludes 0 | 1.000 | −0.949 | −0.775 | 1 |
| **models, within one artifact** | **0.625** (4/4 positive) | −0.750 (0/4 positive) | −0.808 (0/4) | 4 |
| **artifacts, within one model** | **−0.105** (1 of 3 positive: +1.0, −0.32, −1.0) | −0.300 | −0.833 | 3 |

Three findings, in order of how much they should change what anyone does.

**1. The instrument is valid where it can be checked.** `K0` predicts the delta *negatively* and
`degraded` predicts it negatively, in every group, under strict grading — exactly the directions the
design assumes and never fitted to. A model that already knows the material gains less; an artifact
that overwrites correct prior knowledge gains less. Neither is a tautology of the measurement: the
probes and the tasks are different instruments, graded separately.

**2. "For which model tier is this artifact worth loading?" is predicted.** Strict `added` ranks
models inside an artifact with ρ 0.625 and the same sign in all four artifacts.

**3. "Which of my artifacts helps most?" is *not* predicted** — ρ −0.105 across three models, with the
three per-model correlations at +1.0, −0.32 and −1.0. This is the same conclusion §10.3 reached from
the other side: per-artifact `added` is a profile and not a ranking, and the two experiments now agree
from independent directions. **An `added` leaderboard across artifacts is not supported by this data.**

**4. Strict grading carries the signal; lenient does not.** Lenient `added` correlates at 0.126
pooled and 0.000 within-artifact, against 0.620 and 0.625 strict. Partial credit admits answers that
gesture at the right fact, and gesturing does not move a task score. Where a single scoring must be
chosen for a decision, choose strict.

Three limits, all binding. The behaviour sweep covers 4 artifacts × 5 models, so the within-model
groups have 3–4 points each and the ≥10-artifact plan is not met — treat every ρ here as a
direction with an n attached. Only 4 of 12 cells have a task delta whose interval excludes zero, so
8 of the ranks being correlated are partly noise; the sub-analysis restricted to the clean 4 gives
ρ 1.000, which is what a 4-point correlation gives and is quoted only for its sign. And one of the 12
points is a cell where loading the artifact *hurt*: `grok-4.6` on `performance-patterns`, `added` 0.08
against a delta of −0.141 with CI [−0.281, −0.031] (0.397 with the artifact, 0.537 without it). It is
the lowest-`added` cell in the set and also the only clearly negative delta, so it supports the
correlation rather than straining it — but it is a reminder that a low `added` is not merely a waste
of tokens. (The same artifact and model produced §7's deferral collapse in the `root` arm, 0.047,
which is a separate and larger defect.)

The artifact-count limit is now closed: §10.15 re-runs this analysis against the 13-artifact task set
from §10.12, and the "which of my artifacts helps most" correlation goes from −0.105 to **−0.446** on
13 artifacts in one model. Finding 3 below hardens; finding 2 reproduces on the same four artifacts.

### 10.9 Is the tag vocabulary reproducible once extraction is held fixed? (open question O11)

§10.7 found three extractors agreeing on *which spans carry knowledge* and disagreeing on tag rates by
up to 2.5×, worst on `local` and `public` — the two tags §10.1's authoring conclusion rests on. That
comparison could not say whether the extractors label differently or cut the text into different atoms
and label each one correctly. This experiment removes the second cause: two other models re-label the
**same** 2640 reference atom statements over 13 artifacts, under the extraction prompt's verbatim tag
glossary, seeing the statement, its qualifiers and its section and nothing else. κ rather than raw
agreement, because `local` sits on 76% of reference atoms and an all-`local` labeller would score 0.76.

| tag | reference rate | luna rate / κ | grok rate / κ |
|---|---|---|---|
| `routing` | 0.149 | 0.170 / 0.606 | 0.159 / **0.733** |
| `negative-constraint` | 0.171 | 0.210 / 0.665 | 0.201 / **0.711** |
| `version-bound` | 0.028 | 0.022 / 0.664 | 0.022 / **0.699** |
| `evidence` | 0.217 | 0.233 / 0.650 | 0.148 / 0.603 |
| `procedural` | 0.266 | 0.304 / 0.597 | 0.263 / 0.612 |
| `local` | 0.760 | 0.819 / 0.576 | 0.867 / 0.556 |
| `public` | 0.246 | 0.264 / 0.522 | 0.179 / 0.622 |
| `temporal` | 0.105 | 0.286 / **0.399** | 0.160 / 0.636 |
| `rare-critical` | 0.116 | 0.156 / **0.389** | 0.074 / **0.449** |

`type` is the weaker vocabulary: 5-way agreement 0.613 (luna) and 0.524 (grok), κ 0.380 and 0.323.
Both labellers move mass into `motif` (reference 141 probed atoms, luna 323, grok 350) out of
`contract` and `edge`, which is a boundary disagreement about whether an ordered requirement is a
procedure or a contract on an artifact.

**The §10.1 headline survives relabelling.** Re-deriving strict `K0` and `added` on the already-run
probes under each of the three labellings:

| labelling | public K0 | local K0 | public added | local added |
|---|---|---|---|---|
| reference (`claude-opus-5`) | 0.465 | 0.189 | 0.205 | 0.362 |
| `gpt-5.6-luna` | 0.450 | 0.186 | 0.284 | 0.340 |
| `grok-4.6` | 0.486 | 0.214 | 0.241 | 0.351 |

Prior knowledge of `public` material is ~2.2× that of `local` material and the artifact's contribution
runs the other way, under every labeller. The same holds for shape: `motif` has the highest strict
`added` (0.425 / 0.471 / 0.406) and the lowest `K0`, `proposition` the lowest `added` (0.259 / 0.262 /
0.233) and the highest `K0`, in all three labellings.

What this changes, and what it does not:

1. **O1's 2.5× divergence was mostly granularity, not labelling.** With the atom cut held fixed, six
   of nine tags land at κ 0.52–0.73 — moderate to substantial agreement on a 9-way multi-label task —
   and the derived rates move by ≤0.08. The earlier divergence therefore came from the extractors
   producing different atoms, which §10.7 point 5 measures directly.
2. **The authoring recommendation is a property of the knowledge, not of the labeller.** Three
   independent labellers, one of which never saw the reference atom set's provenance, reproduce both
   the direction and roughly the magnitude of §10.1.
3. **The `protection` axis is still the most trustworthy** — `routing`, `negative-constraint` and
   `version-bound` are the top three by mean κ and under grok (under luna, `evidence` at 0.650 edges
   out `routing` at 0.606), agreeing with §10.7's cross-extractor result from a different direction.
4. **`rare-critical` and `temporal` are not gate-grade.** κ 0.389–0.449 on `rare-critical`; luna
   assigns `temporal` at 2.7× the reference rate. A threshold on either would be a threshold on the
   labeller. Note this is the tag that protects safety text from a token-budget cut, so the weakness
   matters more than its size suggests.
5. **A per-tag *gate* is now supported for the protection tags and still not for `type`.** κ 0.32–0.38
   on the 5-way shape vocabulary means a `motif`-specific or `contract`-specific threshold would fire
   on one labeller's boundary; report shape slices, do not gate on them.

Absolute κ needs the same caveat as every other agreement number here: the reference labelling is one
model's opinion, not ground truth, so these are reproducibility figures and not accuracy figures.

### 10.10 The `value` axis was mostly coverage, and the selector knew it

Re-reading the compression inputs rather than its outputs turned up a defect of the same kind as
§5's judging cap. `V(a)` is per *model*, because it multiplies relevance by novelty, and novelty needs
a measured `K0` — which exists only for atoms that were probed. Probing sampled ~15 atoms per
artifact, so the per-model value dictionary is populated on **148 of 2422 judged atoms**:

| artifact | atoms with a per-model `V` | spans carrying one |
|---|---|---|
| dpnp-quickstart | 14 / 83 (0.169) | 17 / 95 |
| mkl-extension-advisor | 9 / 259 (0.035) | 17 / 316 |
| xpu-system-setup | 10 / 259 (0.039) | 6 / 159 |
| performance-patterns | 3 / 279 (0.011) | 4 / 2087 |

Two consequences, and the second is the one that matters:

1. **The reported `value` figures are a weighted retention rate over 3–14 atoms per artifact.** They
   are not wrong, they are thin, and they were quoted without their n. Read §6's `value` column as a
   probe-subset statistic.
2. **`greedy_value` selection was, in practice, atom coverage per token.** The selector scores a span
   as `Σ max(V(a), 1e-4) / tokens` over the atoms it newly covers. With `V` undefined on 97% of atoms
   that floor *is* the term, so the ranking reduces to "how many uncovered atoms does this span carry
   per token", with true value weighting active only on the handful of probed atoms, where it
   dominates. `performance-patterns` had 4 valued spans of 2087.

This does not invalidate §6 or §10.6. Coverage per token is a defensible selection rule, it is what
the non-inferiority results were measured on, and the protection pin — which is what actually
separated the safe candidates from the 0.158 one — never depended on `V`. What it invalidates is the
*name*: an author told "this is your value-ranked shortening" would reasonably expect relevance to
have driven it, and on these artifacts it barely did.

**Engine requirement, implemented:** every shortening candidate now reports `value_coverage` (the
share of atoms whose weight came from a measured relevance *and* outcome rather than the 0.5 fallback)
plus a `value_is_coverage_proxy` flag below 0.5, and `pick_best` tie-breaks on `knowledge` instead of
`value` when the flag is set. Raising the number, rather than hiding it, means probing more atoms per
artifact — the same purchase §10.3 already recommends.

### 10.11 Is knowledge-aware selection worth it, against a generic prompt compressor?

The whole extraction pipeline is only justified if selecting spans by what they carry beats a
compressor that needs none of it. Baseline: **LLMLingua-2**
(`microsoft/llmlingua-2-xlm-roberta-large-meetingbank`), a token-classification model that keeps the
tokens it scores as informative, with the library's recommended structural `force_tokens` so its output
is still readable. Both sides were scored the *same* way — the retention oracle run against the
rendered text, not against a kept-span set, because LLMLingua deletes tokens *inside* a span and a
span-presence score cannot see that — and at the *same* achieved token count, tuning the compression
rate per candidate to land within 3% of the span candidate's tokens.

| artifact | achieved tokens (span / lingua) | knowledge | knowledge strict | protected-atom retention | protected, strict |
|---|---|---|---|---|---|
| dpnp-quickstart | 1392 / 1416 | 0.723 / **0.747** | **0.542** / 0.494 | **0.855** / 0.709 | **0.727** / 0.418 |
| mkl-extension-advisor | 6544 / 6599 | **0.713** / 0.673 | **0.500** / 0.440 | **0.794** / 0.656 | **0.613** / 0.450 |
| performance-patterns | 36014 / 36082 | **0.800** / 0.730 | **0.647** / 0.493 | **0.856** / 0.731 | **0.725** / 0.475 |
| xpu-system-setup | 16413 / 16312 | **0.827** / 0.800 | **0.687** / 0.640 | **0.875** / 0.825 | **0.775** / 0.675 |

Pooled over the eight (artifact, budget) cells, span selection minus LLMLingua-2: knowledge +0.029
(7 cells of 8 in favour), knowledge-strict +0.071 (8 of 8), protected-atom retention +0.115 (8 of 8),
protected-atom strict +0.199 (8 of 8).

1. **The knowledge-aware selector wins, and it wins where the design says it should.** On bulk
   knowledge the margin is small (+0.03, and one artifact went the other way at the tightest budget).
   On *protected* knowledge — negations, gates, thresholds, orderings — it is +0.115 lenient and
   +0.199 strict, in every cell. A token-level compressor drops `not` for one token's saving and keeps
   the surrounding clause, which is precisely the failure the `protection` axis exists to catch.
2. **The margin is largest under strict retention.** +0.071 strict against +0.029 lenient: what the
   generic compressor leaves behind is a partial trace of a statement — enough for the oracle to say
   "related", not enough to say "this knowledge survived". Same lenient-vs-strict lesson as §3.
3. **Token-level compression looks like it *denies* content it damaged.** Atoms whose best NLI
   contradiction against the compressed text exceeds 0.5: LLMLingua-2 is higher on protected atoms
   (0.519 against 0.438 pooled, 6 cells of 8). This is the direction the failure mode predicts, but it
   holds on only the two smaller artifacts and reverses on the other two, so read it as a hypothesis
   with support, not as a measured rate.

Four limits, all binding. **The budgets are not what they say:** the safety-pinned strategy cannot
reach 0.4 on these artifacts, so the 0.4 and 0.6 rows collapse to the same candidate on two of four
and to nearly the same on a third — the effective n is four artifacts, not eight cells, and the
achieved token fractions are 0.58–0.94. **Neither side was run behaviourally**, so this is a retention
comparison, not a task-outcome one; §10.6 is where behaviour lives. **The comparison is scored by the
oracle this study built**, which was validated on constructed pairs (§8) but is the same instrument the
span side is optimized against — an independent judge would strengthen the conclusion. And **the
`value` row is omitted** for the reason §10.10 gives: per-model `V` covered 1–13 atoms in these cells.

### 10.12 Is `shuffled ≥ full` a property of the artifacts or of the probe format? (open question O5)

A probe asks about one atom at a time, and that is the one question shape a scrambled document cannot
be punished by: no single-atom answer needs step 2 to come after step 1. So §3's `shuffled ≥ full`
result could be an artifact of the instrument rather than a fact about the artifacts. The retest is
behavioural and deliberately hostile to the original finding: of 104 generated tasks, the **71 whose
graded `required` items themselves name an ordering** ("do X before Y", "run A first" — 99 such items
across all 13 artifacts), run on three models against three arms (`no_skill`, `full`, and the same
spans re-ordered inside each markdown file with frontmatter pinned first), graded by the same two
graders the behaviour sweep uses. Each answer yields two scores: the task's own score over all
required items, and an **ordering-items-only** score over just the requirements that name a sequence.
213 paired (task, model) cells.

| | `full` | `order_shuffled` | paired diff (95% CI) | cells full / shuffled higher | sign test |
|---|---|---|---|---|---|
| whole-task score | 0.3085 | 0.3056 | +0.0029 (−0.032, +0.039) | 65 / 67 | p = 0.93 |
| ordering items only | 0.3830 | 0.3613 | +0.0217 (−0.027, +0.072) | 46 / 40 | p = 0.59 |

| model | task: full / shuffled | ordering items: full / shuffled | `no_skill` task |
|---|---|---|---|
| claude-opus-5 | 0.429 / 0.377 | 0.419 / 0.389 | 0.227 |
| claude-haiku-4-5 | 0.308 / 0.347 | 0.445 / 0.451 | 0.288 |
| nemotron-nano-12b | 0.188 / 0.193 | 0.285 / 0.245 | 0.092 |

1. **The probe format was not the explanation.** Scrambling order does not cost these artifacts
   anything measurable even on the tasks selected because a wrong order should fail them, and not on
   the very items that name the order. Every per-model CI spans zero in both directions; no model
   drops by more than 0.04 on ordering items.
2. **This is a null with a stated resolution, not a proof of no effect.** The CI excludes an
   ordering-items effect larger than ~0.07 in either direction; a real 0.02–0.05 cost of scrambling
   would sit inside it. What is ruled out is the size of effect the authoring advice turns on.
3. **The instrument had the headroom to see an effect that size.** On the same 213 cells `no_skill`
   is 0.09–0.29 against 0.19–0.43 with an arm, and the share of outright zero-score answers is 0.521
   with no skill against 0.338 (`full`) and 0.310 (`shuffled`). The comparison is not flat because the
   grader cannot discriminate.
4. **Scrambling does not induce deferral either.** Abstentions: 11 (`full`), 10 (`order_shuffled`),
   36 (`no_skill`) — so the arm is not quietly trading an ordering failure for a refusal to answer.
5. **The `full` arm is not winning on ordering items where it wins overall.** `claude-opus-5` gains
   +0.052 on whole-task score and only +0.031 on ordering items; `claude-haiku-4-5` is *behind* on
   whole-task score and level on ordering items. Whatever `full` contributes over the shuffled arm on
   this corpus, it is not sequence.

Three limits. **One shuffle seed per artifact**, so an unlucky permutation cannot be averaged out —
this makes a null weaker than a positive result would have been, because a permutation that happens
to read well is not detectable here. **Markdown only:** script files carry spans too, but permuting a
script's statements produces broken code, and a model punished for that is not being punished for
prose order, so non-markdown files were written in source order — the claim is about prose. **The
ordering-item selection is a regex over behaviour text** (`before|after|first|then|order|sequence|…`),
audited by eye in the report's `matched_behaviors`, and a behaviour that merely contains "first" in a
noun phrase inflates the denominator rather than the effect.

### 10.13 Is term-level attribution real, or decoration? (open question O3)

The span map says which span carries an atom. Highlighting the *words* inside it is the next thing a
UI wants, and it is only honest if removing those words costs the knowledge while removing an equal
number of other words from the same span does not. Ablation, on 260 atoms with exactly one carrier
span of 25–300 tokens (multi-carrier atoms stay recoverable from the other span; spans above 300
tokens dilute both arms toward zero), scored by the retention oracle **against the carrier span
alone**, three conditions per atom: intact, attributed terms masked, and an equal number of distinct
non-attributed content words from the same span masked under a fixed seed. The oracle recovered the
atom from its own intact carrier span for 0.823 of them; the 46 it did not are dropped from every rate
below.

| attributor | n | terms masked / content words | retention: attributed vs random | strict | paired extra loss from attribution (95% CI) | cells attributed worse / random worse |
|---|---|---|---|---|---|---|
| `lexical` (words shared with the statement) | 78 | 6.0 of 30.3 | 0.449 vs 0.840 | 0.115 vs 0.680 | **+0.391** (0.308, 0.474) | 47 / 1 |
| `llm` (model names the minimal carrying terms) | 197 | 5.2 of 20.4 | 0.173 vs 0.627 | 0.031 vs 0.315 | **+0.454** (0.406, 0.503) | 143 / 1 |

1. **Attribution is load-bearing, not decorative.** Masking the attributed words costs 0.39–0.45 more
   retention than masking the same number of other words in the same span, in one direction in all but
   two of 192 decided cells. **E.4's term view is unblocked** on this evidence.
2. **Only the `lexical` result is length-matched.** Both conditions mask the same *count* of words,
   but the LLM attributor returns phrases, so its ablation removes ~23% more tokens (56.1 against
   72.9, intact 75.4) and part of its margin is length. `lexical` masks near-equal length (117.7
   against 119.8), and restricting it to cells matched within 3 tokens keeps the effect at +0.337
   (n = 46). **Take +0.34 to +0.39 as the confound-free figure** and the LLM attributor's +0.45 as an
   upper bound.
3. **The free attributor systematically misses negation.** Under `lexical`, the extra loss on
   `negative-constraint` atoms is +0.143 (n = 14) — the lowest of any tag, against +0.68 on
   `temporal` and +0.50 on `rare-critical` — because negation words are function words and a
   shared-content-word attributor never highlights them. The LLM attributor reaches +0.392 on the same
   tag (n = 37). A term view built on lexical overlap would leave exactly the protection knowledge
   §10.11 shows a compressor destroying un-highlighted.
4. **Ablation shifts the text toward contradiction, not just omission.** Mean best NLI contradiction
   is higher for the attributed ablation than the random one under both attributors (0.285 vs 0.217;
   0.284 vs 0.142) — masking a carrying term leaves a sentence that reads as denying the atom, the
   same failure mode §10.11 measured in token-level compression.
5. **The two attributors are not interchangeable, and the cheap one is weaker where it matters.**
   Mean word-level Jaccard 0.613 over the 211 atoms where both produced terms, with the lexical
   attributor naming almost twice as many terms (9.4 against 5.4) — broader, and still worse on
   negation.

Limits. The usable rows are 78 (`lexical`) and 197 (`llm`) of 260 over all 13 artifacts: the lexical
attributor names ~9 terms in a ~20–30-content-word span, so for most atoms **no equal-sized control
exists** and the row is dropped — which biases the lexical sample toward larger spans, the direction
that *understates* the effect. One ablation seed per atom. Retention only, no behavioural arm, so
"costs the knowledge" means "costs it under the oracle", as in §10.11.

**Correction.** The first run of this experiment keyed its span lookup on `span_id` alone. Span ids are
file-relative (`SKILL.md#001`) and collide across all 13 artifacts, so most atoms were ablated against
another artifact's span; the surviving rows were those the oracle could still recover, which is why
that run reported an intact-recovery rate of 0.496 and smaller effects (+0.322 / +0.480). The figures
above are from the re-run after keying on `(skill, span_id)`. No other tool shared the defect — every
other one filters the span map to a single artifact before indexing it — and §10.14 is what the bogus
0.496 was mistaken for.

### 10.14 How much of the span map is checkable at all? (O3 follow-up)

Every per-span view in the design — dead weight, sole carriers, term highlighting — assumes the cited
carrier span can be confirmed to carry the atom. §10.13 recovers 0.823 of atoms from their own span,
so about one in six citations is not confirmable by the matcher. Two explanations with opposite
consequences: the oracle is too strict on a bare span (the citation is fine, the check is wrong), or
the extractor named a span that does not carry the statement. Separating them is cheap — widen the
text the oracle is given, one nested step at a time, on the same 257 atoms:

| context given to the oracle | mean tokens | recovered (`yes`) | scored | `partial` |
|---|---|---|---|---|
| cited span alone | 72.7 | 0.833 | 0.916 | 0.167 |
| span + heading path | 88.3 | **0.907** | **0.953** | 0.093 |
| span + heading + both neighbours | 241.8 | 0.864 | 0.930 | 0.132 |
| whole section (same heading path) | 1337.4 | 0.837 | 0.916 | 0.160 |
| whole file | 2879.9 | 0.809 | 0.903 | 0.187 |

1. **Most of the missing sixth is a missing subject, and 16 tokens buy it back.** Prepending the
   heading path recovers 25 atoms that the bare span could not support and lifts recovery to 0.907 for
   ~16 tokens. A span like "Use 4 for most workloads" is not a defective citation; it is a sentence
   whose subject lives in its heading. **Consequence: span-level checks, and any per-span view built on
   them, should carry the heading path as part of the span's text.**
2. **More context makes it *worse*, not better.** Recovery falls monotonically after the heading step,
   and at whole-file it is below the bare span. The oracle retrieves the top-4 chunks by similarity
   before running NLI, so a longer text dilutes retrieval and the added chunks displace the carrying
   one. 26 atoms are recovered from the span but not from the file, and 20 the other way. **This means
   retention verdicts are not monotone in the text supplied** — a superset of the text can score lower
   — so a retention comparison is only sound between texts of comparable length. §6 and §10.11 hold on
   this point (candidates there are compared at matched token counts), but no retention number in this
   study should be read as comparable across a 40× length gap.
3. **19% of atoms are not recoverable from their own cited *file*** (49 of 257) — and the extraction
   verifier had called 46 of those `supported` (base rate 17 `distorted` in 257), so this is not the
   known-defect set. They are longer statements (mean 33.0 tokens) and they read as syntheses of a
   whole document: "The default SYCL device is the GPU only when a GPU is visible", "`references/
   official-sources.md` should be loaded when a claim needs checking against upstream documentation".
   **These are real knowledge and the span pointer is a fiction** — the atom was inferred while reading,
   not copied from one place. A per-span view must render them as unlocated rather than colouring a
   span that does not in fact carry them, and the `routing` tag is where they concentrate.
4. **What this does *not* license.** The ladder is a property of one matcher; a second matcher would
   move the 0.19. It does not say the atoms are wrong — `still_missing` and `added` rates never used
   span attribution, so §3 and §10.1 are untouched. It bounds the *views*, not the measurement.

### 10.15 O2 re-run at the planned artifact count (13 artifacts, one model)

§10.8 answered O2 on 4 artifacts × 5 models and flagged the artifact count as the binding limit. The
O5 sweep (§10.12) produced a second behavioural task set — 71 tasks, **all 13 artifacts**, 3 models,
`full` against `no_skill` — so the within-model correlation can now be computed at the planned
artifact count without any new model calls. Same script, same exclusions, `--behavior
data/order_results.jsonl --min-tasks 3`: 24 cells (13 + 6 + 5 artifacts over 3 models),
13–27 paired probes and
3–8 tasks per cell. Data: `reports/exp_novelty_predicts_13art.json`.

| what is being ranked | group (n cells) | strict `added` | strict `K0` | strict `degraded` |
|---|---|---|---|---|
| **artifacts, within one model** | `claude-opus-5` (13) | **−0.446** | +0.501 | +0.008 |
|  | `claude-haiku-4-5` (6) | +0.600 | −0.435 | −0.845 |
|  | `nemotron-nano-12b` (5) | −0.300 | −0.335 | −0.354 |
| models, within one artifact | 4 artifacts × 3 models | **+0.875** (4/4 positive) | −0.227 (1/4) | −0.200 |
| pooled over all 24 cells | — | −0.052 | +0.200 | −0.106 |

Three readings.

**1. The artifact leaderboard is now refuted, not merely unsupported.** At the planned power — 13
artifacts ranked inside one model — strict `added` correlates with the task delta at **ρ −0.446**, and
the three per-model correlations disagree in sign (+0.60, −0.30, −0.45). §10.8's −0.105 was not a
power artifact. **`added` must not be rendered as a cross-artifact ranking**, which is what the engine
already does (`by_knowledge_kind` profiles, no leaderboard) and what the O4 budget result independently
implies.

**2. The failure has a visible mechanism.** In that same 13-artifact group, `K0` correlates
*positively* (+0.50) and `degraded` at ~0 — both wrong-signed against §10.8, where every validity
check pointed the design's way. A cross-artifact comparison varies task difficulty along with the
artifact, and each cell's delta rests on 3–8 tasks (only 8 of 24 have an interval excluding zero), so
the ranking axis inside that group is dominated by which tasks an artifact happens to own. The
validity checks failing exactly where the headline correlation fails is the informative part: it says
*cross-artifact* is the broken comparison, not that the instrument broke.

**3. The model-tier result reproduces but is not independent evidence.** Strict `added` ranks models
inside an artifact at +0.875, positive in all four computable artifacts (the fifth, `model-can-it-fit`,
has identical `added` on all three models and yields no ρ). This is the same 4 artifacts as §10.8 and
23 of the 31 task ids are shared, with the model set cut from 5 to 3 — so it is the same claim
re-measured on a different task subset and a smaller model set, which is worth something and is not a
new sample.

Limits: the task set is the ordering-heavy subset selected for §10.12 (each task's graded requirements
name an ordering), so the deltas describe those tasks and not a uniform task sample; `claude-haiku-4-5`
and `nemotron-nano-12b` have only 5–6 artifacts with enough paired probes, so only the `claude-opus-5`
row meets the ≥10-artifact bar; and probe coverage, not task coverage, is what caps the other two rows.

### 10.16 What the external tool shortlist actually is, and two substrate limits

Everything in §10.1–§10.15 re-analyzes this study's own data. This section and §10.17–§10.20 come
from a separate round that went outside it: 41 candidate tools resolved against the GitHub and
HuggingFace APIs, then four new measurements against the incumbent design. The point of the
resolution pass is that a shortlist is a *licensing* decision before it is a technical one.

Corrections that change whether a tool is usable at all:

| believed | resolved |
|---|---|
| ContextCite at `UKPLab` / `princeton-nlp` | `MadryLab/context-cite`, MIT, **last push 2024-10-08** |
| KGGen at `UKPLab/kggen` | `stair-lab/kg-gen`, **no license** |
| Provence is a repo | HF model only, **cc-by-nc-nd-4.0** — no commercial use, no derivatives |
| AttriBoT usable | `r-three/AttriBoT`, **no license at all** |
| FENICE license unstated, emits no offsets | `Babelscape/FENICE`, **CC BY-NC-SA 4.0**, and it *does* emit sentence offsets |
| VeriScore at `Yale-LILY` | `Yixiao-Song/VeriScore`, Apache-2.0 |
| RefChecker available | **archived** 2025-05 |
| ClashEval / ConflictBank datasets usable | both **unlicensed**; the *method* is reusable, the data is not |

License-clean and active, so eligible: LLMLingua, promptfoo, inspect_ai, MiniCheck (Apache-2.0),
SummaC, AlignScore, tinyBenchmarks, HHEM-2.1-open (`vectara/hallucination_evaluation_model`,
Apache-2.0). Two packaging traps worth recording because both cost a working session: PyPI
`minicheck` 0.4.0 is an unrelated state-machine checker, and AlignScore has no PyPI package and its
inferencer breaks on pytorch-lightning 2.x.

**Substrate limit 1 — no logprobs are available.** Every model on the proxy either accepts
`logprobs` and returns `None`, rejects `top_logprobs`, or errors. So per-token-likelihood methods —
ContextCite as published, and logprob-based knowledge probing — are not unimplemented, they are
*uncapable* on this substrate. Any design that needs them needs a locally served open model first.

**Substrate limit 2 — a measurement cell's model half can disappear, and this is a framework
finding.** Two of the models carrying most of this study's judged `full`-arm cells, `claude-opus-5`
and `gpt-5.6-luna`, now return `400 Invalid model name`. A third, `gpt-6-luna`, rejects
`temperature=0` outright — *"Unsupported value: 'temperature' does not support 0 with this model.
Only the default (1) value is supported"* — which is the setting every graded run here used, so it
cannot serve as an answerer or a judge under the study's own sampling policy. Consequences adopted: a cell must record the *served* model identity, a cell whose model is gone is marked dead
rather than silently re-pointed, and any new arm intended to pair against existing judged data has
to be measured on a model that still resolves.

Not verified, and not to be quoted as verified: the arXiv identifiers for the underlying papers.
`export.arxiv.org` is unreachable from the measurement host; GitHub and HuggingFace are not.

### 10.17 Can a prompt compressor be *told* to protect what span selection protects? (O13 follow-up)

§10.11 showed span selection beating LLMLingua-2 by +0.199 strict on protected-atom retention. The
obvious objection is that the compressor was not asked to protect anything: LLMLingua-2 takes a
`force_tokens` list, and the published baseline only pinned punctuation. If pinning "not", "only",
`--flag` and `≥v2.3` recovers the gap, the atoms, the need graph and the oracle are an expensive way
to reach the same place.

Four artifacts × budgets {0.4, 0.6} = 8 cells, three sides per cell tuned to the same token count
(±3%): the `greedy_value_safe` span candidate; LLMLingua-2 with the punctuation force list
`["\n", ".", "?", ":"]`; and LLMLingua-2 with those plus up to 40 protection terms **mined from that
artifact's own protected atoms** (negation and gate words, flags, versions, size limits; bare
integers excluded because they mine thousands separators). One retention oracle for all three sides.
Paired within cell, never pooled across artifacts.

| pool / grading | contrast | mean paired diff | cells up / down |
|---|---|---|---|
| protected atoms, knowledge | semantic force − punctuation force | **+0.0003** | 4 / 4 |
| protected atoms, strict | semantic force − punctuation force | **−0.0009** | 4 / 4 |
| protected atoms, knowledge | span selection − semantic force | +0.1150 | 8 / 0 |
| protected atoms, strict | span selection − semantic force | **+0.1994** | 8 / 0 |
| all atoms, strict | semantic force − punctuation force | +0.0144 | 6 / 2 |
| all atoms, strict | span selection − semantic force | +0.0561 | 6 / 1 |

**The force list buys nothing on the atoms it was built from** — a coin flip, 4 up and 4 down, and
very slightly negative under strict grading. Span selection keeps its entire protected-atom lead,
unchanged from §10.11 (+0.1994 here against +0.1986 there), so the gap is not an artifact of an
unconfigured baseline.

The mechanism is the same one §10.19 finds in cheap matchers: **pinning a token keeps the word, not
the claim the word governs.** The compressor retains "not" and drops the clause it negated, which is
worse than dropping both — a surviving qualifier attached to nothing reads as prose. A protection
score computed over spans is therefore not replaceable by a token-level compressor however it is
configured.

Limits that shrink n: at budgets 0.4 and 0.6 two artifacts produce identical span candidates, so the
effective cell count is nearer 6 than 8, and the mined list is short on the smaller artifacts (12
terms on one, 32 on another).

### 10.18 Can judge-free items replace the judge? (open question O14)

Every number in this study depends on an LLM judge, and §10.5 shows that judge is least reliable on
exactly the content the artifact contributes most. The cheap alternative is an item with a mechanical
key — multiple choice, or cloze on an exact value — graded by string match with no model in the
grading path.

Built and run end to end: items written by a third model that is neither an answerer nor the atom
extractor, **filtered mechanically before use** (cross-encoder NLI: the keyed option must be entailed
by the atom, each near-miss distractor must not be), answered by two models in `no_skill` and `full`
through the judged run's own context builder, scored by exact match on a parsed `ANSWER:` line. 200
answer calls, no model grading anything.

| model | item type | n | `K0` | `added` | `degraded` | `still_missing` |
|---|---|---|---|---|---|---|
| claude-haiku-4-5 | MCQ | 25 | 0.80 | 0.12 | **0.00** | 0.08 |
| claude-haiku-4-5 | cloze | 25 | 0.12 | 0.40 | **0.00** | 0.48 |
| grok-4.6 | MCQ | 25 | 0.84 | 0.12 | **0.00** | 0.04 |
| grok-4.6 | cloze | 25 | 0.08 | 0.56 | **0.00** | 0.36 |

**1. The mechanical filter is the load-bearing part, and it is free.** 52 sampled atoms produced 27
rejections — **52%**. The rejections are dominated by `distractor_entailed` (the "near miss" was
actually implied by the atom, NLI entailment up to 0.99) and `key_not_entailed` (the generated key is
unsupported, down to 0.00). A generation pass without this filter ships broken keys at scale, silently,
because a broken key still grades cleanly. **Any step where a model writes an answer key gets this
filter**, independent of whether judge-free scoring is adopted at all.

**2. MCQ is near-ceiling; cloze on exact values is the informative type.** `K0` 0.80–0.84 on
four-option MCQ leaves almost no headroom — "which of these is true about X" is answerable from
general competence. Cloze on the same atoms gives `K0` 0.08–0.12 and `added` 0.40–0.56. If judge-free
items enter the framework they enter as cloze on exact values.

**3. They measure a different thing: `degraded` is identically 0.00.** Over all 100 paired items the
census is 46 True→True, 30 False→True, 24 False→False and **zero** True→False, while the judged
lenient band on the same atoms reports `degraded` 0.07–0.46. So the judged `degraded` signal is
largely free-text completeness and qualifier loss, not the model retrieving a wrong fact. Exact-match
items **cannot see this study's main harm signal**, which is the signal the whole `degraded` outcome
exists for.

**4. Agreement with the judged pipeline is not demonstrable at this size.** Per-artifact `added`,
judge-free against judged-lenient: ρ −0.308 and +0.103 on n=5 artifacts; `K0` ρ −0.051 and −0.224.
Judged *strict* is 0.000 everywhere on these subsets — the floor effect §10.3 quantifies — so only the
lenient band is even comparable.

**Decision: judge-free items are adopted as a cheap *additional* instrument for `added`, never as a
replacement for the judged pipeline, and the NLI admissibility filter is adopted unconditionally.**
Replacement would need ≥20 cells of agreement against the judged pipeline, which this does not have,
and would lose `degraded` even if it had them. The `K0` 0.08–0.12 figure is independently corroborated
in §10.20 by a different instrument on different atoms, which is the strongest thing about it: exact
numeric values are very nearly absent from model priors.

### 10.19 Which cheap matcher should stand in front of the adjudicator? (open question O15)

The retention oracle is a cascade: a cheap score decides the two confident ends and the ambiguous band
escalates to a model. The 714-pair bake-off in §"Comparing knowledge" chose that shape; it did not ask
which cheap scorer is best, and the answer turns out to matter more than the shape does.

**What is being measured, stated first because it bounds every number here.** The reference labels are
the *adjudicator's own* verdicts, recovered from the oracle's on-disk cache — 4745 (atom, text) pairs
over 216 candidate texts, with excerpt reconstruction proven byte-exact against the cached scores. So
this measures **which cheap matcher best approximates the expensive adjudicator**, and nothing about
truth. The 773 pairs the incumbent shortcut labelled itself are circular and are excluded, leaving the
hard escalated band, n=3972 (2638 partial / 921 no / 413 yes).

| matcher | AUC "yes" | κ binary | balanced acc | CPU s/1000 | RSS |
|---|---|---|---|---|---|
| **HHEM-2.1-open** | **0.804** | 0.213 | 0.568 | 59.4 | 946 MB |
| MiniCheck | 0.801 | 0.244 | 0.585 | 272.7 | 3475 MB |
| AlignScore | 0.723 | 0.040 | 0.513 | 109.7 | 3081 MB |
| SummaC-ZS | 0.670 | −0.003 | 0.499 | 1533.7 | 1243 MB |
| **nli-deberta-v3-small** (prototype's stage) | **0.576** | **−0.001** | **0.500** | 56.9 | 856 MB |

Majority baselines on this band are 0.896 binary and 0.664 three-class, so every "accuracy near 0.90"
in this band is the degenerate always-not-yes answer, and balanced accuracy is the column to read.

**1. A cross-encoder NLI stage is at chance at its threshold here** — κ −0.001, balanced accuracy 0.500,
with a weak AUC of 0.576.
HHEM-2.1-open reaches 0.804 at the same cost (59.4 vs 56.9 CPU s/1000, 946 vs 856 MB), so this is a
free accuracy gain and not a tradeoff. Note what it is relative to: the **shipped** oracle's cheap
stage is lexical overlap with a [0.25, 0.85] band, which the 714-pair table scores at 0.446 macro-F1
with 0.00 recall on contradictions. HHEM therefore *adds* a stage to the shipped cascade rather than
replacing one.

**2. No cheap matcher removes the LLM adjudication.** A three-class ends-only gate at ≥0.90 agreement
buys **0.003** of adjudications with HHEM and 0.001 or less with everything else; relaxing to 0.80
buys ≤0.0072. The band is 66% "partial" and no monotone score fences that off. Substituting an
aggregate instead of per-pair verdicts fails too — best per-candidate knowledge MAE 0.094, and HHEM's
error-optimal calibration collapses to the constant 0.5. **The escalation is the oracle, and it is not
economizable.**

**3. The length pathology §10.14 found in the oracle is a property of the cheap stage, and HHEM fixes
it.** Mean score by how much surrounding context is supplied (252 atoms): the NLI stage goes
0.556 / 0.716 / 0.641 / 0.652 / 0.670, i.e. **−0.046 from heading to whole file** with 50.8% of atoms
dropping (sign test p = 0.005); HHEM goes 0.530 / 0.705 / 0.770 / 0.776 / 0.752, **+0.047**. SummaC is
catastrophic (0.475 → 0.250). No matcher is monotone and all peak at heading or section level — the
same shape as §10.14's recovery ladder, and the reference LLM's own yes-rate is non-monotone too
(0.830 / 0.903 / 0.861 / 0.838 / 0.810), so a residue of this belongs to the adjudicator.

**4. Every cheap matcher fails on exactly the protected classes.** Agreement against the band mean:
numeric atoms **−0.18 to −0.25**, version-bound −0.07 to −0.10, negation −0.03 to −0.05. The dominant
failure is topical relatedness scoring mid-scale when the *value* is wrong — the text says
`_mm512_reduce_add_epi64` and the atom wants `_mm512_reduce_add_pd`. This is the same defect §10.17
found in `force_tokens` and §10.20 finds in reader models: **cheap instruments see topic, not value**,
which is the structural reason the protection axis stays separate, span-based, and never delegated to a
similarity score.

Licenses are all commercially usable (HHEM-2.1-open Apache-2.0 but needs `trust_remote_code=True`;
MiniCheck's model MIT; nli-deberta, SummaC Apache-2.0; AlignScore MIT). MiniCheck is the fallback if
`trust_remote_code` is unacceptable, at 5× the CPU and 3.7× the RAM for 0.003 less AUC.

### 10.20 If the artifact is wrong, does the model repeat it? (open question O9 follow-up, open question O16)

§10.2 established that loading an artifact makes a model *less* able to resist a reversed premise in
the question, and concluded that content correctness gates before content volume. It did not measure
the case the gate actually has to cover: the premise being wrong **inside the artifact**. Every other
measurement in this document asks what an artifact adds. This one asks what it costs when it is wrong.

**The design, because its credibility rests on the key.** For each candidate atom, a decision-relevant
numeric value is lifted **verbatim** from the artifact and two alternatives are computed
arithmetically from it — `near`, a plausible neighbouring value, and `far`, wrong by orders of
magnitude (`512 MiB` → `1024 MiB` → `32768 MiB`). A model writes only the *question*, and a question
is rejected mechanically if it contains any of the three values. Grading is exact match with a
boundary-aware matcher. **No model writes a key and no model grades**, which is a stronger guarantee
than anything else in this study has.

Four arms per (atom, model), with the planted value substituted at **every** occurrence in the whole
artifact so the planted document is internally consistent: `no_skill`, `full` (unmodified),
`planted_near`, `planted_far`. 20 atoms over 8 artifacts, three model families, 240 answer calls.
Method attribution: this is ClashEval's perturbation design. Its *data* is unlicensed and none of it
is used.

**The control arm is also the item-validity gate.** If the *unmodified* document does not make the
model state V, the question does not target V. That rejected **8 of 60 cells (13.3%)** — one atom's
question asked for physical VRAM while the planted value was *usable* VRAM, and all three models
correctly answered the physical figure from the same document, which a naive scorer reads as three
models heroically resisting a plant. This gate costs nothing, needs no judge, and is the cleanest
admissibility check found anywhere in this study.

Pooled over the 52 question-valid cells (Wilson intervals; the cells are clustered by atom, 20 atoms ×
three models, so these intervals are narrower than an atom-clustered one would be):

| arm | n | states the planted value | 95% CI | states the true value |
|---|---|---|---|---|
| `no_skill` | 52 | 0.019 | (0.003, 0.101) | **0.115** |
| `planted_near` | 52 | **0.942** | (0.844, 0.980) | 0.058 |
| `planted_far` | 52 | **0.846** | (0.725, 0.920) | 0.115 |

**1. Deference is near-total.** A plausible wrong value is repeated 94% of the time. The prior is not
losing a contest with the document; it is not in the contest.

**2. Absurdity barely protects.** Values wrong by 64× — a `32768 MiB` fixed scratch allowance, a
`>95%` regression threshold, `2048 GiB` of VRAM — are still adopted 85% of the time. Paired on the same
(atom, model): 5 cells adopt `near` but not `far`, **0** adopt `far` but not `near`, McNemar exact
two-sided **p = 0.062**. So the effect ClashEval reports reproduces in *sign* and is worth only 0.096
of adoption rate — far too small to rely on as a safeguard.

**3. Models almost never say anything is wrong.** Of 93 adoptions, **3 (3.2%)** flagged the conflict,
all three from one model family, which stated the planted value first and hedged afterwards (*"a >95%
regression … this appears to be a typo in the documentation—it likely means >5%"*). The other two
families flagged nothing, ever. **Silent adoption is the norm**, so no gate may assume a strong reader
notices a bad number.

**4. Where there was a correct prior, it lost every time.** Only 6 of 52 cells had the model state the
true value unprompted — 0.115, which independently corroborates the `K0` 0.08–0.12 that §10.18's cloze
items measured on different atoms with a different instrument. In all 6 of those cells, in **both**
planted arms, the model adopted the planted value: held prior **0/6**. The direction is unambiguous;
the rate is not estimable at n=6.

By value class (planted arms, gated): large integers 24/24 = 1.000, percentages 6/6 = 1.000, versions
42/46 = 0.913, thousands-separated 9/10 = 0.900, **unit-bearing capacities 12/18 = 0.667** — the only
class with meaningful resistance.

**5. What resisted says what to do about it.** Three values out of 20 ever survived a plant: a GPU's
VRAM figure (6 times), a bytes-per-parameter ratio for int4 (twice), and an OS release number whose
*codename* appears unmutated in the same document and in the question (once). The pattern is
consistent and it is not implausibility — it is an **independent anchor**: a hardware SKU the model
knows, an arithmetic identity, or a second unmutated correlate in the same text. **Authoring rule with
evidence behind it: state a decision value together with an independently checkable correlate.** That
is the only mechanism observed to create resistance, and unlike everything else here it is a property
of the artifact rather than of the model.

**Four scoring defects were found and fixed during this run**, recorded because each one silently
inverts the conclusion and each would recur in a reimplementation:

1. A numeric value must match as a **whole value** — `(?<![\d.,])V(?!\d|[.,]\d)`. Substring matching
   makes `10,000` match inside a planted `10,000,000`, which corrupts the classification *and* the
   substitution.
2. The trailing guard must reject a separator **followed by a digit**, not a bare period. Rejecting a
   bare period scored `"…is 40,000."` as the model not stating `40,000`, reading adoption as 0.78
   instead of 0.94.
3. Mutations must stay in decimal notation and inside the legal range. `%g` turns a byte figure into
   `1.56672e+06`, and bumping a utilization fraction to `1.6` refutes itself — a plant that is
   detectable *as a plant* rather than as wrong measures nothing.
4. Item validity must come from the control arm, per above.

Limits: 20 atoms, 8 artifacts, 3 models; the correct-prior subpopulation is 6 cells; **numeric values
only** — identifiers and prose claims are untested and mutating them would put a model back in the key
path; and the two largest artifacts are excluded by the context cap. This therefore does *not* explain
§10.2's reversed-premise effect, which is about premises rather than values.

### 10.21 Re-derivation of the judge band, and four things measured *not* worth building

Zero LLM calls: all of this re-derives from the recorded probe results. Grading convention throughout —
STRICT is `correct_complete`, LENIENT adds `correct_missing_qualifier` and `partially_correct`. Headline
figures exclude the withdrawn model generations (n=1967 of 2143 dual-graded rows).

| quantity | value |
|---|---|
| fine-scale judge agreement, all models | 0.6640 (reproduces §1's 0.664 exactly) |
| fine-scale **Cohen's κ** | **0.5757** — the study reports agreement, not κ |
| binarized κ, STRICT / LENIENT | 0.7409 / 0.6853 |
| STRICT pass rate, grader 1 vs grader 2 | 0.324 vs 0.401 (**+7.7pp**) |
| LENIENT pass rate, grader 1 vs grader 2 | 0.757 vs 0.640 (**−11.6pp**, opposite direction) |
| dual-graded probes having *both* arms for the same model | **25** |
| items (of 573 complete/no_skill/strict) passed by **no** model | **331** |
| items passed by **all** models | **4** |

**1. Quote no `added` difference below 0.15.** The two graders differ by +7.7pp under strict and
−11.6pp under lenient — not a constant offset but a *threshold-dependent* bias in opposite directions,
which is the shape that survives paired differencing. Measured delta-of-deltas across graders is 0.149,
95% CI [0.106, 0.194], on the 25 probes that have both arms dual-graded. For one model the entire
between-artifact `added` spread was 0.20, barely outside that band. This is stricter than the earlier
advice and is adopted as the documented quoting band, not a gate floor. The binding limit is **n=25**: a paired judge band cannot
be measured better without more dual grading, not more probes.

**2. Do not build a stratified probe sampler.** Difficulty stratification buys +0.0074 hit rate
[0.0058, 0.0089]; discrimination stratification +0.0048; both are *worse* than random sampling at 35% of
matched points. Combined with §10.3 — ±0.10 needs ~23 of 44 probes and ±0.05 needs ~35 — the purchase is
probes, not cleverness about which probes.

**3. SliCK's three-way Known/Maybe/Unknown split does not beat flat `K0`** (ρ −0.654 vs −0.595, CIs
overlapping at n=12), and `share_Unknown` is the weakest component at ρ +0.295, p=0.35. The split is
dominated by the grading threshold that defines it. A multi-probe knowledge battery is not free lunch on
this data.

**4. Do not build IRT item cleanup.** The item pool's defect is a **floor**, not answer leakage: 331 of
573 items are passed by no model and 4 by all. Fitting item parameters on that pool models noise. The
suspected leakage is also not leakage — question→answer token containment correlates +0.168
[0.086, 0.250] with pass rate under STRICT and **flips to −0.089** under LENIENT, and an effect that
reverses sign with the threshold is not a property of the items.

Two further defects in how the study's own probe metadata may be read: `style`, `adversarial` and the
`p3` suffix are **perfectly collinear** in this data, so every "adversarial probes are harder" claim
resting on it is unidentified — measured, adversarial items are slightly *easier* to grade. And one
model abstains on 18.7% of atoms, which distorts any comparison against it. Separately, 18–35% of atoms
are answered *confidently wrong* across every paraphrase by every model including frontier ones: that
population, not the unknown one, is what a document has to overwrite, and §10.20 is what happens when
the document it overwrites them with is itself wrong.

## 11. Known gaps in this study

1. **Human gold review is not done.** Sheets exist; absolute levels rest on an LLM judge until they
   are filled. Paired comparisons are much less affected, because a constant judge offset cancels within a pair
   (the threshold-dependent bias of §10.21 does not).
   §10.5 narrows where this matters most: baseline answers and `motif`/`edge` atoms are where the two
   LLM graders diverge, so those are the cells a human pass should sample first, not a uniform sample.
2. **No external primary-source verification.** Temporal and version-bound atoms were checked for
   internal consistency and cross-skill conflict, not against vendor documentation. The 88 conflicts
   say "these disagree", not "this one is wrong".
3. **Behaviour covers 4 of 33 skills, and is under-powered inside those four.** Shortening is
   behaviourally validated only where candidate packages were rendered and run, and §10.6 shows 7–8
   tasks per cell gives a 95% CI ~0.32 wide — wider than every per-cell effect in §6. Pooled across
   the sweep the shorteners clear a 0.05 non-inferiority margin; per artifact they are unproven. More
   tasks per cell, not more skills, is the first thing to buy.
4. **Probe counts per skill are at the floor, so §5 is a profile and not a ranking.** §10.3 measures
   12–29 paired cells per skill and rank correlation collapsing below a 0.75 budget. No gate
   threshold can harden on this data. §10.15 adds the behavioural half: ranking 13 artifacts inside one
   model puts strict `added` against the task delta at ρ −0.446, so the cross-artifact ranking is
   refuted rather than merely unproven, and cross-artifact comparison is where the validity checks
   themselves break down.
5. **Single-lens *labelling*.** §10.7 closes the recall half of this gap on five artifacts: a second
   extractor cites the same spans, so `dead tokens` is not hidden knowledge. What remains open, and is
   now the larger worry, is that the tag vocabulary is not reproducible between extractors (up to
   2.5× divergence, worst on `local` vs `public`), and every per-tag number in §10.1 is conditional on
   one extractor. Eight of thirteen artifacts still have a single extraction pass.
6. **`shuffled ≥ full` is retested and holds, within a stated resolution.** §10.12 ran the comparison
   behaviourally on the 71 tasks whose graded requirements name an ordering and found no cost to
   scrambling (+0.022 on ordering items, CI −0.027 to +0.072). What remains open is a single shuffle
   seed per artifact and an effect smaller than ~0.07, which this data cannot exclude (open question O5).
7. **One atom in five has no locatable carrier, and retention verdicts are not monotone in context.**
   §10.14: 0.809–0.907 of atoms are recoverable from their cited span depending on how much
   surrounding text the matcher is given, with the best result at span + heading path, and 19% are not
   recoverable from their cited *file* at all — inferred-while-reading syntheses whose span pointer is
   a fiction, which every per-span view must render as unlocated. Recovery *falls* as context grows
   past the heading, because the matcher retrieves before it entails, so no retention number in this
   study is comparable across a large length gap. Both need a second matcher before they are treated
   as properties of the corpus rather than of this oracle.
8. **Need frequency `P(t)` is not measured.** All relevance weighting assumes uniform need
   frequency, so `value` ranks atoms *within* one artifact honestly and must not be compared across
   artifacts. Closing this needs real query or issue traffic, not another judge.
