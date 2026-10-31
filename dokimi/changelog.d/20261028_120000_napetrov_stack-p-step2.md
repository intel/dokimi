### Added

- **`benchmark run --judge-temperature`** — a new flag: it sets the judge's sampling
  temperature and is recorded as `judge_temperature` in the eval output. Before this change the
  flag did not exist, and `benchmark run` rejected it as an unknown argument.

### Changed

- **Stack P scores move (#228 step 2)** — the context arm now asks the doc source for the
  `top_k` docs the command was given: `benchmark run` asks for 3 (`DEFAULT_RETRIEVAL_TOP_K`; it
  has no `--top-k`), `dokimi evaluate` and `answers generate` for their `--top-k` (default 3).
  Before, they left `max_results` at the client default of 5. The doc library id resolves
  through the registry's `context7_id` before the client's own resolver, in the `--registry`
  that was loaded. A question with no document (no hit, or the reranker raised) is no longer judged on
  its `[FALLBACK: ...]` placeholder by default, so it drops out of the paired statistics; the
  record and its `retrieval_hit` stay, its diagnosis stays `empty_retrieval`, and the run
  summary says how many questions it left out. Pass `--judge-placeholder` to judge the
  placeholder again (this restores only that part of the old method). `benchmark run`,
  `evaluate` and `eval score` take the same flag; `benchmark batch` has none and never judges
  the placeholder. Scores from before this change are not comparable with new ones.
- **Trust gate** — `retrieval_hit_rate` counts every evaluation's recorded retrieval outcome,
  not only the ones scored in both arms, so unjudged misses still count against the rate, and a
  judge-error row keeps the outcome of its answer. The rate is shown even when no question is
  scored in both arms. A judge-error row of an older eval file, which has no `retrieval_hit`,
  is left out of that count as before. The thresholds are unchanged.
- **Zero-score check** — a judge-error row on a retrieval miss now counts as unusable in
  `zero_score_fraction` (a judge error on a retrieval hit is still left out), so a run can fail
  that fail-severity check where intel/main passed it.
- **Stamped artifacts** — the answers and eval artifacts carry `method_version` (2). Artifacts
  from before this change have no stamp and represent the legacy method (version 1); readers do
  not yet normalize or enforce the version, so it does not yet stop a comparison of mixed methods.
  `eval score` stamps its eval with the version of the answers file it judges, so legacy answers
  re-judged today stay version 1. An eval artifact also carries `judge_placeholder`, so two files
  of one version that differ in it are told apart. A `method_version` that is not a positive
  integer is refused before any judge call (`eval score` exits 2).

### Fixed

- **Stack P no longer ignores `top_k`** — `Answerer` passes `max_results=top_k` to the doc
  source; the context arm's retrieval request is now the one the arms stack makes.
  `answers generate --top-k` takes a positive integer, as `dokimi evaluate` does, and a config
  `retrieval.top_k` that is not one exits 2.
- **Library id from the registry** — `EvaluationPipeline` resolves the library id through
  `resolve_library_id_for_product` (explicit id, then an `mcp:` source's `;id=`, then `context7_id`,
  then the client), in the
  registry that `benchmark run` and `benchmark batch` loaded from `--registry`. A registered
  product without `context7_id` now fails for a client that needs a declared id.

### Removed

- Dead code in the answering path: the unused `Answerer.llm` client and the unreachable
  "no documents" branch of `_generate_with_docs`.
