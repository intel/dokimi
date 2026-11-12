<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

# CLI and artifact changes in development

For agents, CI owners and script authors. These are proposed migrations and open implementations, not commands to paste into an installed release. Use the [current CLI reference](https://intel.github.io/dokimi/reference/cli/) and your installation's help for runnable syntax.

## A consistent evaluation interface

The direction in [#94](https://github.com/intel/dokimi/issues/94) and [#276](https://github.com/intel/dokimi/issues/276) is an evaluation loop with consistent subject groups, model roles, output flags, budgets and verdicts. Some moves have landed; others remain open. The proposal's future command tree is not a second usage guide.

| Change under review | Intended benefit | What consumers need to review |
|---|---|---|
| Routing split, [#595](https://github.com/intel/dokimi/pull/595) | Separate single-skill routing from whole-catalog routing | Proposed `skill routing` and `catalog routing` replace the old routing entry point, with renamed flags. The old spelling becomes a refusal/migration hint, not a working alias. Both new leaves are paid: do not assume the previous no-model/free path survives; review model, provider and budget requirements. |
| JSON envelopes, [#590](https://github.com/intel/dokimi/pull/590) | One envelope around every JSON CLI response | Ten remaining leaves move payload keys under `data`. Update consumers against the actual merged version; do not assume uniformity today. |
| Verdict/exit alignment, [#592](https://github.com/intel/dokimi/pull/592), [#275](https://github.com/intel/dokimi/issues/275) | Distinguish inconclusive, invalid comparison and infrastructure error | Affected commands change codes. An old nonzero-code interpretation may no longer identify the failure class. |
| Registered eval artifacts, [#589](https://github.com/intel/dokimi/pull/589), [#277](https://github.com/intel/dokimi/issues/277) | Discover and validate more saved grading reports | Selected writers gain run metadata, registered schemas and checked writes; not every output is already a registered run artifact. |

**CLI response versus saved artifact:** the command's JSON response envelope and a persisted run file serve different purposes. Do not parse both as the same schema because both contain metadata.

## Budget stops and recoverable work

[#624](https://github.com/intel/dokimi/issues/624) reports that novelty analysis can stop at its spend cap without preserving the paid intermediate work, and that its estimate can be below actual spend. The requested behaviour is partial, stage-labelled evidence and more realistic estimates. The issue does not establish that recovery/resume already works.

A refactor of novelty role clients and costs in [#625](https://github.com/intel/dokimi/pull/625) is not itself a promise to fix those behaviours. Today, keep the command-specific persistence limits in mind and treat an estimate as an estimate. See [reviewing](../reviewing.md) and [known limitations](limitations.md).

## Migration checklist

1. Pin the Dokimi revision used by your automation.
2. Read that version's command help and schema, then the relevant merged change/release notes.
3. Exercise a success, invalid input, failed check and incomplete run for the commands you consume.
4. Update parsers, exit-code policies and stored-artifact readers together where needed.
5. Retain compatibility evidence for your own scripts; a docs build only verifies documentation structure and references.

The [work index](work-index.md) also lists shared stats, judge, container and parser refactors. Those improve consistency and maintainability but should not be advertised as new end-user workflows merely because they have an open PR.
