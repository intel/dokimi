# Benchmarking and comparison guide

This guide describes how to run the supported benchmark flows and how to compare
results. Run all commands from `dokimi/`.

## Comparison modes

`dokimi` has four practical comparison modes:

- **Static snapshot comparison**: compare documentation quality snapshots from
  `python cli.py run`.
- **LLM context comparison**: compare one answer model's context-arm and
  baseline answers. Persisted JSON keeps the legacy `with_docs` and
  `without_docs` keys for compatibility.
- **Multi-model comparison**: run multiple answer models on the same question
  set and compare their evaluated JSON outputs.
- **Treatment-arm comparison**: compare `baseline` against docs, MCP, skills,
  profiles, or agentic tool-use arms.

Keep only one axis variable at a time. For example, a model comparison should
reuse the same questions, doc source, judge setup, and retrieval configuration.

## Static documentation benchmark

Use the static benchmark when the question is "did this documentation snapshot
get structurally better or worse?"

```bash
python cli.py run \
  --root . \
  --spec benchmarks/spec.v1.yaml \
  --out-json baselines/current.json \
  --out-md reports/current.md
```

The JSON snapshot contains summary metrics such as coverage, freshness,
readability, and example pass rate. The Markdown report is a human-readable
view of the same run.

Compare a candidate snapshot with a saved baseline. The repository ships no
committed baseline — the base is a snapshot you generated yourself and kept,
so keep the first `baselines/current.json` you are happy with:

```bash
cp baselines/current.json baselines/baseline.json   # once, after a clean run

python cli.py compare \
  --base baselines/baseline.json \
  --candidate baselines/current.json \
  --spec benchmarks/spec.v1.yaml \
  --out-json reports/compare.json \
  --out-md reports/compare.md
```

The compare command computes candidate-minus-baseline deltas and, when `--spec`
is passed, applies configured regression thresholds. Both snapshots must come
from the same docs corpus definition, otherwise the deltas are meaningless.

Equivalent Make targets are available from the repository root; `BASE` defaults
to `baselines/baseline.json`:

```bash
make benchmark-run
make benchmark-compare                       # uses baselines/baseline.json
make benchmark-compare BASE=path/to/base.json
```

## LLM context benchmark

Use the LLM benchmark when the question is "does documentation or another
context artifact improve answers?"

### Default models

Every command takes its models from `dokimi/defaults.py`, and every surface
(`benchmark`, `answers`, `eval`, `arms`, `subjects`, `evaluate`, `questions`,
`personas`, `novelty`) uses the same three:

| Role | Default | List price, input/output per MTok |
| --- | --- | --- |
| Answer model (the model under test) | `openai/gpt-6.1-sol` | $2 / $10 |
| Judge (also RAGAS, `eval panel-score`, novelty extractor and retention adjudicator) | `anthropic/claude-sonnet-5-5` | $2 / $10 |
| Generator (personas, questions, validation, panel review, gap fill) | `openai/gpt-6.1-sol` | $2 / $10 |

The judge is a different family from the answerer, so the judge-independence
warning stays quiet on default flags; answering with `claude-sonnet-5-5` moves the
novelty judge to `openai/gpt-6.1-sol` and makes the other commands warn. Escalation
judges (`--escalate-model`) have no default; `claude-opus-5-5` ($4 / $20) is the
natural tie-break above the Sonnet judge. On `--provider amazon-bedrock` the bare
names `claude-sonnet-5-5`, `claude-opus-5-5` and `claude-haiku-5-5` resolve to the
`us.anthropic.*` inference profiles of the same name.

**Defaults changed on 2026-10-13: results are not comparable with older baselines;
re-baseline.** Before, `benchmark`/`answers`/`eval` answered with `gpt-5.5` and
judged with `claude-sonnet-4-5-20250929`, while `arms`, `subjects`, `evaluate`,
`questions`, `personas` and `novelty` answered, judged and generated with an
obsolete small OpenAI model. A score, delta or cost produced with default flags before that date
is not comparable with one produced after: re-run the baseline (the same question
set via `--questions-from`, the same arms) under the new defaults, or pin the old
models with `--model`/`--judge-model` to extend an old series. On the surfaces
that used the small model, a default-flag run now costs roughly 13-17x more per token.

For registered libraries, prefer the one-command wrapper:

```bash
python cli.py benchmark run \
  --library onetbb \
  --model gpt-6.1-sol \
  --provider openai \
  --judge-model claude-sonnet-5-5 \
  --judge-provider anthropic \
  --output-dir results/onetbb_sol
```

The run writes:

- `results/onetbb_sol/personas/onetbb.json`
- `results/onetbb_sol/questions/onetbb.json`
- `results/onetbb_sol/answers/onetbb.json`
- `results/onetbb_sol/eval/onetbb.json`
- `results/onetbb_sol/reports/onetbb.md`

The comparison inside one run is always:

```text
delta = context-arm score - baseline score
```

Positive delta means the context helped on that judged question. Negative delta
means either retrieval hurt the answer, the model already knew enough without
context, or the retrieved chunks were irrelevant.

For stability-sensitive claims, run the same configuration several times:

```bash
python cli.py benchmark run \
  --library onetbb \
  --model gpt-6.1-sol \
  --judge-model claude-sonnet-5-5 \
  --judge-provider anthropic \
  --multi-run 3 \
  --output-dir results/onetbb_sol
```

Multi-run mode creates numbered run directories and prints the mean and
standard deviation of the context-arm average.

## Fair multi-model comparison

A fair model comparison must use the same questions for every model. First
create or choose a seed run:

```bash
python cli.py benchmark run \
  --library onedal \
  --model gpt-6.1-sol \
  --judge-model claude-sonnet-5-5 \
  --judge-provider anthropic \
  --output-dir results/onedal_seed
```

Then reuse that question set for each model:

```bash
python cli.py benchmark run \
  --library onedal \
  --questions-from results/onedal_seed \
  --model gpt-6-astra \
  --provider openai \
  --judge-model claude-sonnet-5-5 \
  --judge-provider anthropic \
  --output-dir results/onedal_astra

python cli.py benchmark run \
  --library onedal \
  --questions-from results/onedal_seed \
  --model claude-opus-5-5 \
  --provider anthropic \
  --judge-model claude-sonnet-5-5 \
  --judge-provider anthropic \
  --output-dir results/onedal_opus55
```

Interpret model comparisons using two separate signals:

- **Absolute quality**: the context-arm average from each run.
- **Context benefit**: the average `delta` from each run.

Do not rank models by delta alone. A strong model can have a small delta because
its baseline score is already high.

Generate a cross-run dashboard when you have multiple eval files:

```bash
python cli.py dashboard generate \
  --results-dir results \
  --output-dir reports
```

The dashboard reads evaluation artifacts and writes `reports/DASHBOARD.md` and
`reports/dashboard.json`.

## Standalone pipeline commands

Use the lower-level commands when you need explicit control over each artifact:

```bash
python cli.py answers generate \
  --product oneTBB \
  --questions <content-checkout>/questions/onetbb_golden.json \
  --output results/onetbb_golden/answers/onetbb.json \
  --model gpt-6.1-sol \
  --provider openai \
  --debug-retrieval

python cli.py eval score \
  --product oneTBB \
  --answers results/onetbb_golden/answers/onetbb.json \
  --output results/onetbb_golden/eval/onetbb.json \
  --judge-model claude-sonnet-5-5 \
  --judge-provider anthropic

python cli.py report eval \
  --product oneTBB \
  --eval results/onetbb_golden/eval/onetbb.json \
  --out results/onetbb_golden/reports/oneTBB_full.md
```

Use `report generate` instead of `report eval` when you also want the questions
file included in the report generator:

```bash
python cli.py report generate \
  --product oneTBB \
  --eval results/onetbb_golden/eval/onetbb.json \
  --questions <content-checkout>/questions/onetbb_golden.json \
  --output results/onetbb_golden/reports/oneTBB.md \
  --format markdown
```

## Treatment-arm comparison

Use treatment arms when comparing context artifacts rather than models:

```bash
python cli.py arms run \
  --product oneTBB \
  --questions <content-checkout>/questions/onetbb_golden.json \
  --arms "baseline,docs,skill:<content-checkout>/skills/onetbb-quickstart" \
  --judge \
  --out-json results/arms/onetbb_skill.json \
  --out-md results/arms/onetbb_skill.md
```

The `baseline` arm is the control. Every other arm is compared against it. Keep
the answer model and judge fixed when comparing arms.

### Reading the significance tables

Arms are compared **paired per question** against the one shared `baseline`
sample: a question contributes a pair only when both arms produced an aggregate,
so the delta varies by treatment rather than by a re-drawn control.

Because one run tests several arms at once, p-values are corrected for
multiplicity — with several arms, the chance that at least one clears 0.05 by
luck is well above 0.05. `--stats-correction` picks the method:

```bash
python cli.py arms run ... --stats-correction bh
```

- `holm` (default) — Holm-Bonferroni, controls the family-wise error rate. The
  honest choice with one baseline and a handful of arms.
- `bh` — Benjamini-Hochberg, controls the false discovery rate. Use it when
  screening many arms and some false positives are acceptable.

Arm contrasts and factorial-interaction contrasts are corrected as **separate
families** — pooling them would inflate the multiplier on both — and each row
records its `correction_family`, `family_size`, and `family_tested`.

The **Sig.** column follows the *adjusted* p-value, not the raw one. Three
values are possible:

| Sig. | Meaning |
|---|---|
| `yes` | adjusted p < 0.05 **and** Wilcoxon < 0.05 when it could be computed |
| `no` | the tests ran and did not clear 0.05 |
| `—` | no test could run — fewer than 6 complete pairs, or the deltas are constant (a degenerate sample, where Cohen's d_z is *unbounded* rather than negligible) |

A `—` is not a null result. Cohen's d_z and the p columns render `—` in the same
situation rather than printing `0.00`, which would read as a measured no-effect.

The 95% CI is a Student-t interval on the paired deltas, so it excludes zero
exactly when the t-test rejects; `ci_method` records which estimator produced it
(`student_t`, `percentile_bootstrap`, or `degenerate`).

The judged artifact carries the whole block at
`summary.per_arm.<arm>.significance`, so the table above is renderable from the
JSON alone.

When arm names form a `a`, `b`, `a+b` triple (see `CombinedTreatment`), the
artifact also gets `summary.interactions`: the contrast
`AB − A − B + baseline` computed per question against one shared baseline, which
is what tells you whether combining two treatments beats the sum of their
separate effects. Running the arms separately and subtracting run averages does
not answer that question — the baselines drift between runs.

Everything above assumes the judge's continuous 0–100 score. The
execution-verified task track (`cli.py tasks run`) has a binary outcome per task
and uses McNemar's exact test instead — see
[coding-harnesses.md](coding-harnesses.md).

### Pinning sampling

Answer generation and grading are both sampled calls, so two identical runs
differ. These flags pin them, and every value is recorded in the artifact so a
pinned run cannot be mistaken for a free-sampling one:

```bash
# models that still accept a temperature (Claude Haiku 4.5, gpt-oss, Qwen3, ...)
python cli.py arms run ... --temperature 0 --judge --judge-temperature 0
```

Current reasoning models accept only `temperature=1` (litellm refuses any other
value before the request is sent): Claude Opus 4.7 and later, the 5.x Claude
models (`claude-haiku-5-5`, `claude-sonnet-5-5`, `claude-opus-5-5`,
`claude-fable-5-1`, on Anthropic and on Bedrock), and OpenAI `gpt-5`, `gpt-5.5`
and `gpt-6.1-sol` (`gpt-5.1` and `gpt-5.2` accept a temperature unless a reasoning
effort is set, which dokimi does not do). The run stops at the probe below, before
any answer is generated, so on these models leave
`--temperature` and `--judge-temperature` unset. Their sampling cannot be
pinned, only measured: run the same configuration several times
(`benchmark run --multi-run N`; for `arms run`, repeat the run into separate
`--out-json` files) and quote the spread.

- `--temperature`, `--seed`: sampling for answer generation, recorded in
  `run_config`. Omitted from the request when unset.
- `--judge-temperature`: sampling for the judge, recorded as
  `judge_temperature`. Applies to a single judge and to every role of a panel.
  `eval judge-validity --judge-temperature` certifies the judge under the same
  setting, and records it in the validity report. `eval score`, `eval
  panel-score` (every panel judge) and `evaluate` take the same flag and record
  it as `judge_temperature` in their output. So do `eval pairwise` (and its
  escalation judge), `eval checklist`, `eval judge-wording`, `eval cross-judge`
  (every `--judges` entry) and `eval judge-validity --mode pairwise`; each
  probes every judge model with the setting first and exits 2 if one rejects it.
- `--judge-panel technical_expert,developer_advocate,doc_reviewer`: grade with
  several judge roles and record their spread and agreement. An unknown role is
  rejected when the command line is parsed.

A flag the provider is known to reject (Bedrock rejects `seed`) fails the run
before any answer is generated. The check reads litellm's capability table, so
a model that table misdescribes still fails per answer. A value the provider
rejects (`temperature=0` on a model that accepts only 1) is caught by one short
probe call per answer and judge model, also before any answer is generated.

The judge re-draws a reply it cannot parse, up to three attempts. At a pinned
judge temperature the same prompt tends to return the same malformed reply, so
each re-draw then appends a short format reminder to the prompt; the first
attempt is unchanged. `judge_metrics.redrawn_judgements` counts the judgements
that needed a re-draw.

`temperature=0` is not a determinism guarantee. It picks the highest-scoring
token, but some endpoints compute slightly different scores from one call to
the next, so reproducibility has to be measured per endpoint.

### Trusting the judge

A significance table says the delta is unlikely under *this judge's* noise. It
does not say another judge would agree, and on the perf golden set it did not:
the same skill-vs-baseline answers scored +1.7 under the stored judge and −3.4
under Haiku 4.5 (both significant). Four commands make that visible.

**1. Gate the judge first.** `eval judge-validity` checks that the judge orders
answers whose order is known (an answer above its own damaged copy).
`--sequential` adds bases until each degradation is decided instead of stopping
at a fixed 20, which is usually inconclusive: across five seeds the fixed gate
said PASS once and FAIL four times, all inconclusive; the sequential gate said
FAIL five times.

The sequential test separates accuracy `gate - 0.08` from `gate + 0.08` at 5%
error each way (the default 0.80 gate tests 0.72 against 0.88). Above a gate of
0.91 the band narrows so the upper hypothesis stays at 0.99 (gate 0.95 tests 0.91
against 0.99), and `--sequential` exits 2 for a gate outside 0.03 to 0.97, where
no feasible number of pairs decides; the report prints the pair it used. Near
the top of that range the cap matters: a judge at 0.99 ends inconclusive in
about 7% of runs at gate 0.96 and 29% at gate 0.97 with the default
`--max-bases 120`, and about 5% at gate 0.97 with 240. A
sequential FAIL on one degradation settles the run (`conclusive` is true) even
while another is still undecided; `inconclusive_variants` still names it.

`--concurrency` (default 5) runs judge calls in parallel; before it the check was
serial. The verdict does not depend on it: each intact answer is scored once, a
failed intact score is retried up to three times before its variants are scored,
and rows keep pair order. A throttled call that still fails becomes an errored
pair, which counts as a miss, so lower it when the summary shows errors.

```bash
python cli.py eval judge-validity --answers run.json --sequential \
  --judge-provider amazon-bedrock --judge-model openai.gpt-oss-120b-1:0 \
  --mode pairwise --output validity_pairwise.json
```

The report gives, per
degradation, the pairs scored, the gate accuracy with its 95% CI and PASS or
FAIL; `--sequential` adds a `mode` line and marks a degradation still undecided
at the stop `continue`, with the reason under INCONCLUSIVE (cap reached, or the
answer pool exhausted). Exit 1 on FAIL, and on an inconclusive pass with
`--strict`.

`--mode pairwise` shows the judge both answers, in both orders, and asks which
is better. On the perf gate gpt-oss-120b pairwise passed every degradation
(pooled 0.96, $0.47 measured) where absolute gpt-oss, Haiku 4.5 and Opus 5.5
each failed one; absolute Opus 4.6 also passed, at about 26× the cost per pair.
`--escalate-model` re-asks the pairs the cheap judge answered inconsistently
across the two orders with a stronger one (0.99 pooled, 38 of 355 pairs
escalated). A gate pass is a floor, not a guarantee: gpt-oss was
order-consistent on 95% of gate pairs but on only 44% of real skill-vs-baseline
comparisons, which are much closer calls.

**2. Compare arms the same way.** `eval pairwise` puts each arm's answer next
to the baseline's; `--escalate-model` is the same cheap-then-expensive cascade.

**3. Check the verdict across judges.** `eval cross-judge` re-scores an arms run
under several judges (absolute, and with `--grounded` against each question's
`expected_topics`), takes `eval pairwise` and `eval checklist` reports as more
measures, and labels each arm `CONSISTENT ±`, `LEANING ±`, `JUDGE-DEPENDENT` or
`NO DETECTABLE EFFECT`, on Holm-adjusted p over the measures that vote. With
`--validity` reports, a judge that failed its gate is shown but does not vote
(its grounded and checklist measures included); `--require-validated` leaves
only judges that passed. The stored scores are labelled from the file's
`judge_provider`/`judge_model` (`provider/model (stored)`), so a `--validity`
report for that judge reaches them and a verdict resting on it alone reads
`(one judge)`. Re-scoring resolves each arm's context the way the arms run does;
from a file that is the report snippets. Since `judge_context` the stored judge
saw each treatment's full material, which the file does not keep, so with
`--judges`, `--pairwise` or `--checklist` and stored scores for such an arm the
command refuses (exit 2): the other judges see only snippets or answers.
`--no-stored` compares those judges with each other. The gate itself (and `eval pairwise` / `eval checklist`) passes no
context, so it validates a judge only in the no-context setting. A label whose significant measures all rest on one judge says `(one judge)`; an escalated pairwise measure counts as its cheap judge's. Gate a judge on
answers from the same answer model it will judge: `cross-judge` warns when the
validity report was measured on another model's answers.

```bash
python cli.py eval cross-judge --answers run.json --questions golden.json --grounded --both \
  --judges amazon-bedrock/us.anthropic.claude-haiku-5-5,amazon-bedrock/openai.gpt-oss-120b-1:0 \
  --pairwise run_pairwise.json --validity validity_haiku.json --validity validity_pairwise.json
```

**4. When changing the judge prompt**, `eval judge-wording` re-runs the gate
under meaning-preserving rewrites of it and flags a degradation FRAGILE when a
rewrite moves it on the same pairs: a paired sign test against the original
prompt, Holm-adjusted over every rewrite and variant. Re-draws of the original
are shown as a noise floor but do not decide the flag. Use at least 30 bases; at
20 bases only a large wording effect is detectable.

`eval checklist` asks, per expected topic and in both polarities, whether the
answer covers it. Its sign was stable across judges, but it is the weakest of
the measures (p ≈ 0.05 where pairwise reached 0.002) and costs about 2.7× an
absolute judgement (Haiku, measured: $0.015 vs $0.0055 per answer); its value is
the list of points an arm added or lost.

## Multi-model comparison

Use `report model-compare` to compare judged treatment-arm JSON files from
multiple models.

The intended input is one judged arms JSON file per model for regular questions
and/or golden questions. The report extracts the `baseline_arm` score as the
baseline and the treatment arm as the context arm, then computes:

- overall context-arm, baseline, and delta per run,
- statistical significance of context-arm minus baseline (paired t-test,
  Wilcoxon signed-rank, Cohen's d_z), corrected across the compared runs \u2014
  every run tests the same "did the treatment help?" question, so they form one
  family; `--stats-correction holm|bh` selects the method and the table shows
  the raw p alongside the adjusted one. scipy is a hard dependency, so these are
  always computed; they report `None` only when a run has fewer than
  `eval.stats.MIN_PAIRS_FOR_TEST` (6) complete pairs or the deltas are constant,
- difficulty breakdown on common question IDs,
- head-to-head winners on common question IDs,
- ranking by absolute context-arm score and by delta.

**All summary, significance, difficulty, and head-to-head metrics use only the
common question set** (intersection of question IDs across all runs).

```bash
python cli.py report model-compare \
  --regular-runs results/arms/dpnp_regular_sonnet46.json results/arms/dpnp_regular_opus48.json \
  --golden-runs  results/arms/dpnp_golden_sonnet46.json  results/arms/dpnp_golden_opus48.json \
  --run-ids sonnet46,opus48 \
  --out results/dpnp_compare.md
```

Regular and golden options may be used independently:

```bash
# Regular questions only
python cli.py report model-compare \
  --regular-runs results/arms/dpnp_regular_sonnet46.json results/arms/dpnp_regular_opus48.json \
  --run-ids sonnet46,opus48 \
  --out results/dpnp_regular_compare.md

# Golden questions only
python cli.py report model-compare \
  --golden-runs results/arms/dpnp_golden_sonnet46.json results/arms/dpnp_golden_opus48.json \
  --run-ids sonnet46,opus48 \
  --out results/dpnp_golden_compare.md
```

When a run contains more than one non-baseline arm, specify which one to score:

```bash
python cli.py report model-compare \
  --regular-runs run_a.json run_b.json \
  --run-ids a,b \
  --treatment-arm with-skill \
  --out compare.md
```

Consistency is validated before the report is written:

- Different `baseline_arm` values across runs in the same group abort with an
  error.
- **Different treatment arms** across runs abort with an error: each run's delta
  would measure a different treatment (one run's `profile:` arm beside another's
  `skill:` arm is confounded, not a model comparison). Pin the shared arm with
  `--treatment-arm` to override — it makes the choice explicit, and a run missing
  that arm then fails on its own.
- **One arm name carrying two different prompt digests** aborts with an error. A
  `profile:` arm is named after the profile's frontmatter `id`, so editing a
  profile in place leaves old and new cells sharing a name while measuring
  different prompts; pooling them averages two treatments. Artifacts written
  before provenance stamping carry no digest and are not affected.
- Diverging `question_set_hash` values (when present) produce a warning on
  stderr.
- Runs that share one configuration (model, provider, judge, judge prompt and
  temperature, `run_config` except the seed value, harness, plugin set, cell and
  arm list) are replicates: an error,
  unless `--pool-replicates` averages them per question into one row. A
  question is kept only if every replicate scored both arms; the report says
  how many were dropped.

The report names the arm it scored rather than calling everything "Context arm":
"Models Compared" gains a **Treatment arm** column (per run, or `— (baseline
only)`), and the summary, ranking, and benefit headers read the arm name — e.g.
`### By absolute quality (profile:concise_expert)`. The generic label survives only
when the runs disagree or no arm was scored. The name is taken from the extracted
score records, not the artifact header, so the label cannot disagree with the
numbers beside it.

For programmatic use, import from the package directly:

```python
from dokimi.report.model_compare import (
    check_run_consistency,
    generate_combined_report,
    load_run,
    non_baseline_arms,   # the arm names in one artifact other than its baseline
    prompt_digests,      # the profile_prompt_sha256 values stamped for one arm
)
```

## Caching

Two independent caches sit behind the LLM chokepoint
(`dokimi/caching.py`). Both are about cost, not about results: the text
the model sees is byte-identical whether they are on or off.

### Provider prompt caching

Providers charge a fraction of the input price for a request prefix they have
already seen. Anthropic-family models need an explicit breakpoint
(`cache_control`); OpenAI and Gemini cache long prefixes on their own.

Two conditions must hold, and the second used to fail silently here:

1. The breakpoint has to be set — done automatically, on prefixes of at least
   12000 characters (`MIN_CACHEABLE_PREFIX_CHARS`). Below the provider's minimum
   the prefix is ignored and the write surcharge is pure loss; the minimum is
   1024 tokens for most Anthropic models and 2048 for the small ones, so at ~4
   characters per token 12000 is the first round figure that clears the higher
   floor with margin.
2. The reused text has to be an actual **prefix**. The answer prompt now puts
   the injected context *before* the question for exactly this reason. Earlier
   versions asked the question first, so a 12k-character skill document could
   never be a reusable prefix and `cache_hit_ratio` was always 0.0%.

   **This reorder changes model output.** Scores from runs made before it are
   not directly comparable with scores made after.

Only *question-independent* context is offered for caching. A skill body is the
same for every question (`context_is_static=True`); `docs` retrieval is reranked
per query, so annotating it would pay the write surcharge on every call and never
hit. A combined arm is cacheable only when every context-bearing part is.

The judge rubric is deliberately not cached: at ~1–2k characters it is far below
the provider minimum.

`DOKIMI_PROMPT_CACHE=0` turns the breakpoints off (default: on). The
`Cache hit` column in the arms report reads `cache_hit_ratio` from
`cost_summary`, which is `cache_read_tokens / prompt_tokens` as the provider
reported them — so the column is now a real measurement rather than a constant
zero.

### Response caching

Off unless `DOKIMI_RESPONSE_CACHE` names a directory:

```bash
DOKIMI_RESPONSE_CACHE=.cache/responses python cli.py arms run ...
```

A byte-identical request (same model, message text, streaming flag, `max_tokens`,
and `temperature`/`seed` when set) replays the stored completion instead of
calling the provider. The `utils.get_llm` path (Bedrock, Vertex, OpenRouter)
keys on model, text, `max_tokens` and `temperature`/`seed` when set, without the
streaming flag, so its entries never collide with `llm_call`'s; a given judge
always goes down one path, so this costs nothing. It
pays off mainly for the `baseline` arm, which is identical across treatment runs,
and for re-running a pipeline after changing only the reporting code. The judge
is covered on every provider, including Bedrock, Vertex and OpenRouter, which
reach the model through a separate wrapper.

A request can also carry a **draw** index, for when a caller wants a second,
independent sample of the same prompt: the judge's retry after an unparseable
reply asks for a new draw, so the retry is a real second attempt rather than the
same cached reply read again. Draw 0 adds nothing to the key, so entries written
before draws existed still hit. A judge reply that does not parse is neither
stored nor, if an older run stored it, replayed; otherwise a question whose three
attempts all failed once would fail on every later run without calling the
model. A cold cache is therefore not needed for the retry to be real.

The cache is not judge-only. It applies to every caller of `llm_call` and
`utils.get_llm`, which includes question generation (`questions/llm_gen.py`),
persona generation (`personas/generator.py`) and the question validator
(`questions/validator.py`), on every provider; for `openai` and `anthropic`
those already went through the cached `llm_call` path before the judge was
covered. With the variable set, regenerating questions or personas from the
same inputs returns the previous set. Unset it (or point it at an empty
directory) when you want new ones.

The key is computed over the message **text**, not the request structure, which
is what keeps the two caches independent: a prompt-cache breakpoint splits one
string into two `text` blocks without changing a character of what the model
reads, so hashing the raw structure would make `DOKIMI_PROMPT_CACHE=0`
discard every entry already paid for. `max_tokens` *is* in the key — a truncated
completion is a different answer.

`DOKIMI_RESPONSE_CACHE` accepts the same off values as the prompt-cache
switch (`0`, `false`, `no`, `off`, empty), so disabling one the way you disable
the other does not create a cache directory named `0`. Pointing it at an existing
*file* is a configuration error and raises, rather than presenting as a cache
that never hits.

A replayed entry reports the **stored** usage record — tokens, cost and latency
as measured when the call was really made, not `$0`/`0s`, so cost stays
comparable across runs. The flip side: latency from a cached run is a recording,
not a measurement. That is why this cache is opt-in, and why it should stay off
for any run whose latency numbers you intend to quote.

## Baselines

Save meaningful eval outputs as named baselines:

```bash
python cli.py baseline save \
  --from-eval results/onetbb_sol/eval/onetbb.json \
  --name onetbb-gpt55-docs
```

Compare a future eval against a saved baseline:

```bash
python cli.py baseline compare \
  --baseline onetbb-gpt55-docs \
  --eval results/onetbb_candidate/eval/onetbb.json
```

Use baselines for release-to-release tracking. Use `--questions-from` for
model-to-model fairness.

## What to commit

Commit curated *inputs* under `data/` when the framework itself needs them:

- `data/skills/fastcontext/SKILL.md`
- `data/agent_profiles/*.md`
- `data/explore_tasks/*.yaml`

Question sets and product skills are evaluation content: commit them to the
content repository and pass them in with `--questions` / `--questions-root` and
`skill:<path>`.

Answer runs, judge scores and static-benchmark snapshots are outputs, not
fixtures: keep them under `results/`, `reports/`, `answers/`, `eval/` or
`baselines/`, which are git-ignored. Their schemas are locked by
`dokimi/schemas/*.json` and `tests/test_artifacts.py`; the only
committed artifacts are the one-per-schema fixtures in `tests/fixtures/artifacts/`,
which every later edit of that schema version must still accept. Producers write a
registered kind only through `dokimi.artifacts.save_artifact`, which
validates before an atomic write, so an invalid run fails on its first incremental
save. A reader's test builds its input with `tests/artifact_fixtures.py`, which also
rejects keys the schema does not declare: the schema lists every record key its
producers write, so a fixture cannot invent one.
