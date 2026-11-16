<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Added

- **`subjects evidence`: each result says when it may be compared.** A result now carries `metric_spec` (unit, range, direction, judged), the `verdict_rule` its verdict follows (`p_adjusted` below `alpha`), and `protocol {id, version, digest}`: the digest is `sha256` over the canonical JSON of the resolved measurement inputs (judge snapshot, sampling, judge prompt hash, eval-set digest, repeats, test, correction, thresholds). A missing input or an alias such as `latest` is listed in `protocol.unresolved` and left out of the digest. `comparable`/`non_comparable` say why a result cannot be compared. `dokimi.subjects.evidence_check` holds the consumer checks, with conformance fixtures in `tests/fixtures/evidence_conformance`. Matrix-rollup and scorecard cells now carry the judge the run recorded (`judge_model`, `judge_provider`, `judge_context`, `judge_temperature`, `judge_prompt_sha256`). Runs do not record a repeat count, and a judge named by a bare family name (`opus`, `sonnet`) is an alias, so such results list those inputs as unresolved and are not comparable across runs.
