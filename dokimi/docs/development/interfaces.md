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
| Output flags, [#591](https://github.com/intel/dokimi/pull/591) (merged): merged on main, not in 0.2.0 | One spelling for an output file (`--out`) and an output directory (`--out-dir`) | `--out-json` becomes `--out`, `--output-dir` becomes `--out-dir`, and the directory `--out` of `repo run`, `repo reach`, `repo skill-reach`, `eval check` and `eval tasks generate` becomes `--out-dir`. The old spellings print the new command and exit 2, so a script written for 0.2.0 must change the flag. |
| Routing split, [#595](https://github.com/intel/dokimi/pull/595) (merged on main) | Separate single-skill routing from whole-catalog routing | `skill routing` and `catalog routing` replace the old routing entry point, with renamed flags. The old spelling becomes a refusal/migration hint, not a working alias. Without `--model` they use the free lexical floor; with `--model` they call a model and cost money, so review the model, provider and budget requirements. |
| JSON envelopes, [#590](https://github.com/intel/dokimi/pull/590) (merged on main) | One envelope around every JSON CLI response | Every `--json` leaf emits the `cli_output.v1` envelope on main, with the payload under `data`. Read `data` rather than the top level. |
| Verdict/exit alignment, [#592](https://github.com/intel/dokimi/pull/592) (merged), [#275](https://github.com/intel/dokimi/issues/275) | Distinguish inconclusive, invalid comparison and infrastructure error | Affected commands change codes. An old nonzero-code interpretation may no longer identify the failure class. |
| Registered eval artifacts, [#589](https://github.com/intel/dokimi/pull/589) (merged), [#277](https://github.com/intel/dokimi/issues/277) | Discover and validate more saved grading reports | Selected writers gain run metadata, registered schemas and checked writes; not every output is already a registered run artifact. |

**CLI response versus saved artifact:** the command's JSON response envelope and a persisted run file serve different purposes. Do not parse both as the same schema because both contain metadata.

## Budget stops and recoverable work

[#624](https://github.com/intel/dokimi/issues/624) (closed) asked for partial, stage-labelled evidence from capped novelty runs and for estimates closer to actual spend. On main, `skill novelty analyze` keeps a partial record when the cap stops it; this page has not verified the stage labels or the estimate changes. The issue does not establish that recovery/resume already works.

A refactor of novelty role clients and costs in [#625](https://github.com/intel/dokimi/pull/625) (merged) is not itself a promise to fix those behaviours. Today, check each command's persistence notes (for `skill shorten`, pass `--cache-dir` so a capped run can be replayed) and treat an estimate as an estimate. See [reviewing](../reviewing.md) and [known limitations](limitations.md).

## Migration checklist

1. Pin the Dokimi revision used by your automation.
2. Read that version's command help and schema, then the relevant merged change/release notes.
3. Exercise a success, invalid input, failed check and incomplete run for the commands you consume.
4. Update parsers, exit-code policies and stored-artifact readers together where needed.
5. Retain compatibility evidence for your own scripts; a docs build only verifies documentation structure and references.

The [work index](work-index.md) also lists shared stats, judge, container and parser refactors. Those improve consistency and maintainability but should not be advertised as new end-user workflows merely because they have an open PR.
