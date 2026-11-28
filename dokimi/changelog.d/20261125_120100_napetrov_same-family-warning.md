### Added

- **a judge or extractor of the answering model's vendor family is warned about** — eval judge runs (`arms run`, `evaluate`, `benchmark`, `orchestrate`, the judge re-checks) print one warning when the judge is a different model of the answerer's vendor (for example `claude-haiku-5-5` judging `claude-sonnet-5-5`). `skill novelty analyze` warns when the extractor is of the answerer's vendor. Warnings only: nothing is refused, and the same-model wording and the stored `evaluator_independence_warning` flag are unchanged. The vendor table moved from `dokimi.novelty.provenance` to `dokimi.utils`.
- Vendor warnings and novelty provenance use one model-family lookup. A model routed through another provider keeps its own vendor, and Mistral pairs now record `same_vendor: true`.
