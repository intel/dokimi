<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

# Known limitations and reliability work

Use this page when interpreting results or choosing a workflow. This is a snapshot of open work reviewed on 11 November 2026, not a claim that every issue reproduces on every installation. Verify the linked issue against your version. Start with the working guides; use these limits to qualify conclusions.

| Area | Limitation or open defect | What to do today | Tracked work |
|---|---|---|---|
| Budget and recovery | `skill novelty analyze` and `skill shorten` can lose paid work at the cap; on main, not 0.2.0, `atoms` and `probe` keep it in a `.partial.json` file; estimates can understate spend | Start small, inspect per-command persistence, and do not promise a recoverable capped `analyze` run | [#624](https://github.com/intel/dokimi/issues/624), [#457](https://github.com/intel/dokimi/pull/457) |
| Guidance delivery | Whole-file read counters omit searches through grep/rg | Inspect traces when delivery matters; do not translate zero reads into “never seen” | [#626](https://github.com/intel/dokimi/issues/626) |
| Receipt provenance | Saved loading receipts do not fully pin the harness profile | Keep the profile revision with the experiment and qualify replayed expectations | [#350](https://github.com/intel/dokimi/issues/350) |
| Delegation | Fork-context skills can escape the intended no-delegation condition | Inspect actual skill configuration and traces before claiming single-agent execution | [#148](https://github.com/intel/dokimi/issues/148) |
| Harness support | Harbor MCP servers, compose sidecars and separate verifier environments are unsupported | Choose supported tasks/environments; do not score unsupported setups as model inability | [#355](https://github.com/intel/dokimi/issues/355) |
| Repeat independence | Cached answers and changed question content can undermine headroom/repetition interpretation | Preserve question digests and cache provenance; count independent trials separately | [#356](https://github.com/intel/dokimi/issues/356), [#370](https://github.com/intel/dokimi/issues/370), [#596](https://github.com/intel/dokimi/pull/596) |
| Judge trust | Self-judge aliases, panel completeness and lexical grounding can mislead | Inspect judge identity, missing votes and evidence semantics; agreement is not proof | [#309](https://github.com/intel/dokimi/issues/309), [#407](https://github.com/intel/dokimi/issues/407) |
| Statistical families | Tests incapable of reaching significance can still enter some Holm families | Review the comparison path and contributing cases; do not generalize a fix from one report to all reports | [#478](https://github.com/intel/dokimi/issues/478), [#573](https://github.com/intel/dokimi/issues/573), [#574](https://github.com/intel/dokimi/pull/574), [#606](https://github.com/intel/dokimi/pull/606) |
| Numeric edge cases | Extreme values, small variance and skewed small samples expose statistical edge cases | Investigate non-finite outputs and assumptions before interpreting a verdict | [#542](https://github.com/intel/dokimi/issues/542), [#598](https://github.com/intel/dokimi/issues/598), [#599](https://github.com/intel/dokimi/issues/599), [#600](https://github.com/intel/dokimi/issues/600), [#601](https://github.com/intel/dokimi/issues/601) |
| Path and encoding handling | Pinned paths, non-UTF-8 names and Windows decoding have open edge cases | Keep source provenance and inspect errors rather than treating them as failed evals | [#582](https://github.com/intel/dokimi/issues/582), [#608](https://github.com/intel/dokimi/pull/608), [#566](https://github.com/intel/dokimi/issues/566), [#597](https://github.com/intel/dokimi/issues/597), [#602](https://github.com/intel/dokimi/issues/602), [#611](https://github.com/intel/dokimi/issues/611), [#612](https://github.com/intel/dokimi/issues/612), [#613](https://github.com/intel/dokimi/pull/613) |
| Malformed inputs | Some empty, invalid or deeply nested inputs raise tracebacks or parse slowly | Validate input files and classify the failure as input/infrastructure, not model performance | [#576](https://github.com/intel/dokimi/issues/576), [#581](https://github.com/intel/dokimi/issues/581), [#604](https://github.com/intel/dokimi/pull/604), [#605](https://github.com/intel/dokimi/issues/605), [#607](https://github.com/intel/dokimi/pull/607), [#619](https://github.com/intel/dokimi/issues/619) |
| Imported task files | Hub file resolution can ignore the requested data directory | Verify which files were imported before running the task | [#412](https://github.com/intel/dokimi/issues/412) |
| Trace timing | Dropped trace lines can lose evidence before the first useful action | Treat incomplete traces as incomplete evidence | [#347](https://github.com/intel/dokimi/issues/347) |

## What a fix does not establish

A parsing fix does not validate a grader. A registered artifact does not prove a complete experiment. A static warning is not a behavioural security finding. An umbrella issue remaining open does not mean all its components are absent.

Other scanner correctness, performance, release and CI fixes are linked in the [complete work index](work-index.md). Their presence there preserves traceability without turning implementation housekeeping into another product feature.
