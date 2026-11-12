<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

# Run checks through an agent or CI

For coding agents operating Dokimi and maintainers integrating checks into automation. Start from an explicit subject, scope and output location; select the same workflow a human would use.

## Produce a key-free catalog report

After [installation](README.md):

```bash
dokimi catalog eval /path/to/catalog --tier static --out-dir /path/to/receipts
```

The output directory must be outside the catalog checkout and its ancestors, and empty or contain only this command's earlier receipts. No model, Docker, bundled skill script or task verifier runs in this static tier.

**Outputs:** `catalog.json`, `plan.json`, `result.json` and `report.md`. Exit 0 means deterministic checks passed; 1 means validation findings; 2 means unusable input/output. A static pass does not establish task performance. See [catalog receipt semantics](../external-content.md) before implementing a gate.

## Run repeatably

- Pin Dokimi, subject revision, content, harness and model configuration.
- Validate input and output contracts for the selected command; do not assume every command shares the same JSON format or exit codes.
- Preserve failures, partial runs and missing measurements. An absent result is not a pass.
- Estimate paid work and use only the budget controls supported by that track. Keep credentials in the execution environment.
- Keep receipts with the tested revision; redact sensitive content before sharing artifacts that contain source text.

Use [runs list/show](../runs.md) to inspect supported stored artifacts without new model calls. Use [baselines and comparison workflows](../benchmarking-and-comparison.md) for regression gates, after establishing a qualified evaluation set.

## Know the implementation boundary

Existing checks and baseline workflows are usable. A universal autonomous optimize/publish/revalidate lifecycle is not implemented as one complete workflow; see [continuous evaluation cards](../features/continuous-evaluation.md).

**Done when:** the operator can tell validation failure, execution failure, insufficient evidence and a measured regression apart, with a report tied to the input revision.

[Skill route](skills.md) · [Repository route](repositories.md) · [Result interpretation](results.md)

To change Dokimi itself rather than operate it, follow [contributor guidance](contributors.md).

[CI and evidence lifecycle plans](../development/README.md#repeatable-ci-and-evidence) · [CLI migration notes](../development/interfaces.md)
