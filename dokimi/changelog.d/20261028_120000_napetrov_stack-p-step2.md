### Changed

- **Stack P scores move (#228 step 2)** — the context arm now asks the doc source for the
  `top_k` docs the command was given: `benchmark run` asks for 3 (`DEFAULT_RETRIEVAL_TOP_K`; it
  has no `--top-k`), `dokimi evaluate` and `answers generate` for their `--top-k` (default 3).
  Before, they left `max_results` at the client default of 5. `benchmark run --judge-temperature`
  now reaches the judge (it used to be dropped), and the doc library id resolves through the
  registry's `context7_id` before the client's own resolver, in the `--registry` that was
  loaded. A question with no document (no hit, or the reranker raised) is no longer judged on
  its `[FALLBACK: ...]` placeholder by default, so it drops out of the paired statistics; the
  record and its `retrieval_hit` stay, its diagnosis stays `empty_retrieval`, and the run
  summary says how many questions it left out. Pass `--judge-placeholder` to judge the
  placeholder again, which restores the old numbers. `evaluate` and `eval score` take the same
  flag. Scores from before this change are not comparable with new ones.
- **Trust gate** — `retrieval_hit_rate` counts every evaluation's recorded retrieval outcome,
  not only the ones scored in both arms, so unjudged misses still count against the rate, and a
  judge-error row keeps the outcome of its answer. The rate is shown even when no question is
  scored in both arms. The thresholds are unchanged.
- **Stamped artifacts** — the answers and eval artifacts carry `method_version` (2). Older
  artifacts have none and read as version 1. An eval artifact also carries `judge_placeholder`,
  so two files of one version that differ in it are told apart.

### Fixed

- **Stack P no longer ignores `top_k`** — `Answerer` passes `max_results=top_k` to the doc
  source; the context arm's retrieval request is now the one the arms stack makes.
- **`benchmark run --judge-temperature`** reaches the judge pipeline.
- **Library id from the registry** — `EvaluationPipeline` resolves the library id through
  `resolve_library_id_for_product` (explicit id, then `context7_id`, then the client), in the
  registry that `benchmark run` and `benchmark batch` loaded from `--registry`. A registered
  product without `context7_id` now fails for a client that needs a declared id.

### Removed

- Dead code in the answering path: the unused `Answerer.llm` client and the unreachable
  "no documents" branch of `_generate_with_docs`.
