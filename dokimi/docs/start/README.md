<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

# Start here

Choose the outcome you need; you do not need to learn every Dokimi command first.

| Your role | Follow this route |
|---|---|
| Skill author or third-party skill reviewer | [Check and improve a skill](skills.md) |
| Repository maintainer | [Check repository guidance](repositories.md) |
| Evaluator, researcher, or docs/MCP author | [Compare context or agent setups](experiments.md) |
| Coding agent or CI operator | [Run repeatable checks](automation.md) |
| Dokimi contributor | [Extend and maintain Dokimi](contributors.md) |

## Install

For a published release, with Python 3.11 or newer:

```bash
python -m pip install dokimi
dokimi --help
```

For functionality described as available on main, use a source checkout. From the **outer repository root**:

```bash
cd dokimi
python -m pip install -e .
dokimi --help
```

This enters the package directory containing `pyproject.toml`. The guides here use the installed `dokimi` command, so you do not need to find `cli.py`. Relative input and output paths are resolved from your current working directory; use absolute paths when running elsewhere.

## Bring the subject you want to check

Dokimi is the evaluation framework. Your skills, golden question sets and executable tasks usually live in your own checkout. [External content](../external-content.md) explains formats and validation. Paths such as `/path/to/catalog` below are replacements, not bundled examples.

Start with a static check. It needs no model credentials and makes no LLM calls. Model experiments need the selected provider or logged-in CLI; coding tasks also need a supported execution environment. See [credentials and costs](../reviewing.md) and [harness support](../coding-harnesses.md) before launching paid work.

## What to do after a check

Read [results and next steps](results.md). A finding, an incomplete check and a failed agent task need different actions. A free check passing is not evidence that the skill improves outcomes.
