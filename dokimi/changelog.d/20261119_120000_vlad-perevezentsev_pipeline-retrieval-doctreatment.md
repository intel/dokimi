<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Changed

- **`evaluate`, `benchmark run` and `answers generate` retrieve docs through the arms stack's path (#228 step 3).** The context arm now retrieves through `DocTreatment`, as `arms run` does: one request (`max_results` from `--top-k`, `max_tokens` from `--max-tokens` in `answers generate` and `benchmark run`, default 4000; `evaluate` always sends 4000) and the same docs kept (the top 3 after rerank). The request is the one these commands sent before, so the Context7 cache still hits. One case answers differently: when the reranker raises, the context arm answers from the first 3 retrieved docs, as arms runs do, instead of recording the `[FALLBACK: No relevant documentation found]` placeholder. Answers files are stamped `method_version` 3; a run whose reranker failed on some question scores differently from a version 2 run of the same questions. With `--debug-retrieval`, `retrieval_metadata` drops `avg_score` and gains `arm_kind`, `library_id` and, when a step failed, `error` (`retrieval_failed` or `rerank_failed`). When the reranker raises, `top_score` in this metadata and in the `arms run` docs arm's metadata is `null` unless the doc source scored the docs (it was `0` in `arms run`); `arms run` does not change otherwise.
