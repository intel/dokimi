<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

# Extend and maintain Dokimi

For contributors adding commands, metrics, sources, harness support or documentation. To evaluate your own skill or repository, use the other [starting routes](README.md).

1. Read [AGENTS.md](../../../AGENTS.md), [contributing](../../../CONTRIBUTING.md), and the [architecture](../architecture.md) before editing.
2. Choose the relevant extension guide: [metric](../contributing-metric.md), [document source](../adding-doc-source.md), or [executable task](../contributing-terminal-bench-task.md). Evaluation content generally belongs with its subject, not in the framework.
3. Read the [user journeys](../user-journeys/README.md) as the product contract and [traceability](../user-journeys/traceability.md) for acceptance evidence. These are design and verification resources, not a mandatory end-user tutorial.
4. Update the owning guide and feature card when behavior changes. Link new pages in site navigation. Preserve existing public anchors.
5. Run required checks from the package directory, including the strict documentation build. Follow repository contribution rules for commits and PRs.

**Done when:** behavior, commands, documentation and acceptance evidence agree, with any validation limitations recorded in the PR.

[Release process](../releasing.md) · [Research register](../research-register.md) · [Complete document index](../documentation-index.md)

## Preview and publication

From the inner `dokimi/` directory with the development and documentation dependencies installed:

```bash
mkdocs serve
```

Open the local URL printed by MkDocs. Check the home page, your audience route, feature cards and navigation on narrow and wide screens. `mkdocs build --strict` checks the site build and internal links; it does not establish visual usability.

For a PR, the docs workflow saves the built site as the **docs-preview** artifact for seven days. Download and extract it, then serve the extracted directory with `python -m http.server 8000` to inspect it locally. This is a downloadable preview, not a public preview URL.

A push to `main` builds and publishes [GitHub Pages](https://intel.github.io/dokimi/). An open PR does not change the public site. Documentation reflects main; installed releases may have fewer capabilities.

[All open work](../development/work-index.md) · [Development directions](../development/README.md)
