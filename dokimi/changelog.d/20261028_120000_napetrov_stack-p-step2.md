### Changed

- **Stack P scores move (#228 step 2)** — `benchmark run` and `orchestrate evaluate` now
  answer with the `--top-k` they were given (retrieval asked for `max_results` 5 and the
  client default before), take the judge temperature from `--judge-temperature` (it used to
  be dropped), and resolve the doc library id through `products.yaml` `context7_id` before the
  client's own resolver. A question with no document (no hit, or the reranker raised) is no
  longer judged on its `[FALLBACK: ...]` placeholder by default, so it drops out of the paired
  statistics; the record and its `retrieval_hit` stay. Pass `--judge-placeholder` to judge the
  placeholder again, which restores the old numbers. `eval score` takes the same flag.
- **Trust gate** — `retrieval_hit_rate` counts every evaluation's recorded retrieval outcome,
  not only the ones scored in both arms, so unjudged misses still count against the rate. The
  thresholds are unchanged.
- **Stamped artifacts** — the answers and eval artifacts carry `method_version` (2). Older
  artifacts have none and read as version 1.

### Fixed

- **Stack P no longer ignores `top_k`** — `Answerer` passes `max_results=top_k` to the doc
  source; the context arm's retrieval request is now the one the arms stack makes.
- **`benchmark run --judge-temperature`** reaches the judge pipeline.
- **Library id from the registry** — `EvaluationPipeline` resolves the library id through
  `resolve_library_id_for_product` (explicit id, then `context7_id`, then the client). A
  registered product without `context7_id` now fails for a client that needs a declared id.

### Removed

- Dead code in the answering path: the unused `Answerer.llm` client and the unreachable
  "no documents" branch of `_generate_with_docs`.
