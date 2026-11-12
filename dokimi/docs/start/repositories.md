<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

# Check repository guidance

For maintainers who want coding agents to navigate, build, test or review their repository more reliably.

## 1. Run a free scan

After [installation](README.md):

```bash
dokimi repo scan /path/to/your/repository
```

**Input:** your local repository. **Cost:** no LLM calls. **Result:** findings about paths, commands and identifiers referenced by guidance. Review `fail` and `unresolved` findings against the actual repository, correct stale instructions, and rerun. The scan does not establish that every instruction is semantically true or that the agent follows it.

A manifest is not required for the initial scan. See [the review workflow](../reviewing.md#a-repositorys-agent-setup) for default guidance discovery and an explicit manifest.

## 2. Check that instructions reach the agent

Use the [repository reach probe](../repo-evaluation-contract.md#7-reach-probe-guidance_reachv1) and [review workflow](../reviewing.md#a-repositorys-agent-setup) to select a supported harness, model and budget. Begin with a dry run:

```bash
dokimi repo reach /path/to/your/repository --model YOUR_MODEL_ID --dry-run
```

Replace `YOUR_MODEL_ID` with a model supported by your selected harness. `--dry-run` validates and prints the plan; it calls no harness and produces no loading evidence. After reviewing the plan, credentials and budget, rerun without `--dry-run` to execute the paid probe. The [conformance matrix](../reach-matrix.md) tests fixture scenarios and is background evidence, not a substitute for probing your own repository. Loading depends on harness, file location and task activity; seeing a file in the repository is not evidence of delivery.

**Result of the executed probe:** loading evidence for the tested scenarios. Fix discovery before judging whether unseen content helps.

## 3. Measure the effect on real work

Prepare the repository's task manifest, environment, reference solutions and graders using the [repository evaluation contract](../repo-evaluation-contract.md). Validate tasks and controls before paid agent runs. Compare guidance conditions on the same tasks; use the [rule ledger](../rule-ledger.md) to retain reasons for instructions.

**Result:** task outcomes and comparison evidence. Without tasks, report a static/loading review, not an outcome improvement.

**Done when:** actionable guidance findings are resolved or explained, loading is tested for the chosen harness, and any claimed improvement is backed by representative task comparisons.

[Prepare a repository](../preparing-a-repository.md) · [Repository feature cards](../features/repositories.md) · [Read results](results.md)

[Instruction-effectiveness development](../development/README.md#instruction-delivery-and-adherence) · [Current limitations](../development/limitations.md)
