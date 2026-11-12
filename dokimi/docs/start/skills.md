<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

# Check and improve a skill

For skill authors, catalog maintainers and people assessing a third-party skill. Start with the artifact you have; no golden question set is needed for the first check.

## 1. Check the packaging

After [installation](README.md), point at your skill catalog:

```bash
dokimi catalog check --root /path/to/catalog --kind skills
```

**Input:** a local catalog containing skills. **Cost:** no LLM calls. **Result:** structural findings about whether the catalog and skills load. Fix reported problems and rerun. This does not execute bundled scripts or prove usefulness.

For a single skill or a full review, follow the [review workflow](../reviewing.md#a-skill). For machine-readable catalog receipts, use the [automation route](automation.md).

## 2. Decide what needs measuring

- If the agent never selects the skill, investigate [discovery and routing](../features/discovery.md) with real user requests and actual competing skills.
- If the content may be redundant or incomplete, use [knowledge diagnostics](../skill-novelty-walkthrough.md). Estimate cost before probing.
- If you want to claim improved answers or code, prepare representative questions or executable tasks and use [a controlled comparison](experiments.md).

A key-free estimate for a local skill bundle:

```bash
dokimi skill novelty analyze --artifact skill:/path/to/skill --tier triage --include-resources --estimate-only
```

**Result:** an estimate, not a measured quality score. Follow the walkthrough to configure models, judge and budget before removing `--estimate-only`.

## 3. Make one change and retest

Keep the original skill revision and the same evaluation set. Edit the passages implicated by evidence; then compare outcomes. Familiar content can still serve a procedural or routing purpose, so novelty alone does not justify deletion.

**Done when:** your review identifies what was checked, findings, costs, unmeasured claims and the next experiment; an improvement claim has task evidence for the tested revision and setup.

[Read the result](results.md) · [All skill features](../features/skills.md) · [Recorded novelty studies](../skill-novelty-evidence.md)

[Upcoming skill and catalog capabilities](../development/README.md#skill-bundles-and-catalogs) · [Guided improvement plans](../development/README.md#guided-improvement)
