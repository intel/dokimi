<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

# Open work index

Snapshot of all 109 open issues and pull requests in `intel/dokimi`, reviewed on **11 November 2026**. This inventory includes maintenance and bugs, not only features. Open means open at review time, not scheduled or unimplemented in full. The linked issue/PR is the live source; check merged code before carrying an old umbrella claim into usage documentation.

Read the [development guide](README.md) for user outcomes, [interface migrations](interfaces.md) for automation impact, and [known limitations](limitations.md) for result interpretation. Individual feature availability remains in the [feature cards](../features/README.md).

## Capabilities and user workflows

| Source | Kind at review | Work item |
|---|---|---|
| [#41](https://github.com/intel/dokimi/issues/41) | Open issue | Repository evaluation: move the eval mechanics out of oneDAL dev/agent_evals into dokimi |
| [#48](https://github.com/intel/dokimi/issues/48) | Open issue | judge-validity: validate the judge in the setting arms are scored — persist judge material by digest |
| [#94](https://github.com/intel/dokimi/issues/94) | Open issue | CLI API: the evaluation loop at the top level, subject and eval groups below, one enforced flag vocabulary (proposal v2.1) |
| [#95](https://github.com/intel/dokimi/issues/95) | Open issue | User journeys J1–J14: catalog and criterion-level evidence tracking |
| [#96](https://github.com/intel/dokimi/issues/96) | Open issue | J4: adherence and task-impact evidence for instructions (AC-J04-005, AC-J04-006) |
| [#97](https://github.com/intel/dokimi/issues/97) | Open issue | J7: draft tasks, negative controls and coverage gaps (AC-J07-004, AC-J07-006, AC-J07-007) |
| [#98](https://github.com/intel/dokimi/issues/98) | Open issue | J2: join the novelty diagnosis to a task-level effect (AC-J02-004, AC-J02-005) |
| [#99](https://github.com/intel/dokimi/issues/99) | Open issue | J11/J12: distinct inconclusive verdict and blocking policy (AC-J11-002, AC-J12-002) |
| [#121](https://github.com/intel/dokimi/issues/121) | Open issue | User loop: eval engine contracts (plan/validate/compare/gate/record), 3 skills, GitHub Action |
| [#138](https://github.com/intel/dokimi/issues/138) | Open issue | Skill catalog integration (intel/skills): per-skill evals in catalog CI, results, promotion to validated |
| [#148](https://github.com/intel/dokimi/issues/148) | Open issue | repo run: block fork-context skills under --delegation none |
| [#220](https://github.com/intel/dokimi/issues/220) | Open issue | Skill auto-improvement: measurement gates (A) and improve command (B) |
| [#275](https://github.com/intel/dokimi/issues/275) | Open issue | CLI exit codes: audit every command against the 0/1/2 rule (#94 A2) |
| [#276](https://github.com/intel/dokimi/issues/276) | Open issue | CLI: move commands into the new groups, stage M1–M3 (#94) |
| [#277](https://github.com/intel/dokimi/issues/277) | Open issue | Artifact envelope for run artifacts, not inputs (#94 B1/B1b) |
| [#318](https://github.com/intel/dokimi/issues/318) | Open issue | GitHub Action: pr-check / record / revalidate, .github/dokimi config, per-subject baselines |
| [#331](https://github.com/intel/dokimi/issues/331) | Open issue | eval coverage: coverage of a skill's problem space and relevance of each eval, reproducible (need graph + optional interview) |
| [#342](https://github.com/intel/dokimi/issues/342) | Open issue | Configuration Effectiveness (agents.md#213): receipt, reach matrix, rule ledger, arms |
| [#350](https://github.com/intel/dokimi/issues/350) | Open issue | Pin the harness profile to a commit in the effective-instructions receipt |
| [#354](https://github.com/intel/dokimi/issues/354) | Open issue | Fidelity contract per arm: golden payload diff in CI vs the real harness, starting with Claude Code |
| [#355](https://github.com/intel/dokimi/issues/355) | Open issue | Harbor features unsupported: MCP servers, compose sidecar services, separate verifier environment |
| [#360](https://github.com/intel/dokimi/issues/360) | Open issue | Behavioural security for skills: sandboxed runs, trap tasks, trace-based gate |
| [#407](https://github.com/intel/dokimi/issues/407) | Open issue | Judge reliability: connect multi-judge pieces and verify evidence |
| [#413](https://github.com/intel/dokimi/issues/413) | Open issue | Corpus-scale scan gaps (catalog scan, novelty accuracy) |
| [#424](https://github.com/intel/dokimi/issues/424) | Open issue | catalog granularity: decide whether skills should be split, merged, or regrouped (static check + paid partition eval) |
| [#441](https://github.com/intel/dokimi/pull/441) | Open PR | feat(eval): skill coverage — uncovered skill items as candidate checks (#220 A5) |
| [#454](https://github.com/intel/dokimi/pull/454) | Open PR | fix(validate): read the when cue of each language in description-no-when |
| [#455](https://github.com/intel/dokimi/pull/455) | Open PR | feat(catalog): check the scripts a skill bundles |
| [#459](https://github.com/intel/dokimi/pull/459) | Open PR | feat(catalog): catalog check --online reports missing packages and dead links |
| [#472](https://github.com/intel/dokimi/pull/472) | Open PR | novelty: program checks on probes (identifiers veto) + grading-ladder design note |
| [#482](https://github.com/intel/dokimi/pull/482) | Open PR | docs: judge-panel study of the skill-novelty headline numbers |
| [#489](https://github.com/intel/dokimi/pull/489) | Open PR | feat(novelty): judge panel (--judge repeatable, --judge-aggregate) |
| [#556](https://github.com/intel/dokimi/pull/556) | Open PR | feat(catalog): path-missing, a warn for missing bundle paths in code spans |
| [#557](https://github.com/intel/dokimi/pull/557) | Open PR | feat(catalog): allowed-tools-unrestricted-bash warn |
| [#558](https://github.com/intel/dokimi/pull/558) | Open PR | feat(catalog): not-a-skill-no-frontmatter, with a suggested frontmatter block |
| [#559](https://github.com/intel/dokimi/pull/559) | Open PR | feat(catalog): bundle-file-unreferenced, bundled files SKILL.md never names |
| [#588](https://github.com/intel/dokimi/pull/588) | Open PR | feat(repo): repo security, unsafe agent behaviour per arm against the baseline (#360) |
| [#589](https://github.com/intel/dokimi/pull/589) | Open PR | fix(eval): eval writers go through save_artifact (#277 R4) |
| [#590](https://github.com/intel/dokimi/pull/590) | Open PR | feat(cli): every --json leaf emits cli_output.v1 |
| [#592](https://github.com/intel/dokimi/pull/592) | Open PR | fix(cli): exits 3, 4 and 5 per the §2.6 table; a crash exits 5 |
| [#593](https://github.com/intel/dokimi/pull/593) | Open PR | feat(repo): scan without a manifest checks the rules and @ imports Claude Code loads |
| [#594](https://github.com/intel/dokimi/pull/594) | Open PR | feat(eval): eval graph builds the frozen need graph for eval coverage (#331) |
| [#595](https://github.com/intel/dokimi/pull/595) | Open PR | feat(cli): skill routing and catalog routing replace novelty routing (#94 M3) |
| [#596](https://github.com/intel/dokimi/pull/596) | Open PR | feat(eval): eval headroom does not count cached replays as fresh reps |
| [#624](https://github.com/intel/dokimi/issues/624) | Open issue | novelty analyze: a --max-usd stop writes nothing it paid for, and --estimate-only runs below the real bill |
| [#626](https://github.com/intel/dokimi/issues/626) | Open issue | repo: guidance_read does not count a grep or rg over a guidance file, so delivery reads undercount |

## Reliability, evidence and compatibility

| Source | Kind at review | Work item |
|---|---|---|
| [#19](https://github.com/intel/dokimi/issues/19) | Open issue | docs: rewrite out-of-docs links on the element tree instead of the raw Markdown |
| [#100](https://github.com/intel/dokimi/issues/100) | Open issue | Journeys: bind available but untraced criteria to tests (AC-J03-002, AC-J03-005, AC-J12-001, AC-J14-005) |
| [#309](https://github.com/intel/dokimi/issues/309) | Open issue | Same-model warning misses a Bedrock profile against a bare anthropic id |
| [#347](https://github.com/intel/dokimi/issues/347) | Open issue | First-useful: keep the events before the first dropped trace line |
| [#356](https://github.com/intel/dokimi/issues/356) | Open issue | eval reference: stamp a digest of the questions file so headroom catches an edit at the same path |
| [#370](https://github.com/intel/dokimi/issues/370) | Open issue | record replay provenance per answer in arms artifacts so headroom can exclude cached repeats |
| [#412](https://github.com/intel/dokimi/issues/412) | Open issue | task-formats: hub data_files= ignores data_dir= (resolved against task dir) |
| [#432](https://github.com/intel/dokimi/issues/432) | Open issue | order_independence gate message still cites the shuffled-arm win |
| [#434](https://github.com/intel/dokimi/issues/434) | Open issue | catalog check --per-skill: load skill bodies one at a time, not all up front |
| [#478](https://github.com/intel/dokimi/issues/478) | Open issue | Holm's multiplier counts results that cannot reach p < 0.05 (arms and repo compare) |
| [#522](https://github.com/intel/dokimi/issues/522) | Open issue | docs/repo-evaluation-contract.md says a busy batch exits 2 |
| [#523](https://github.com/intel/dokimi/issues/523) | Open issue | stale exit-code text in unreleased changelog fragments on main |
| [#542](https://github.com/intel/dokimi/issues/542) | Open issue | eval: warn when the sign-flip test runs on a skewed contrast at small n; NaN delta is not 'large' |
| [#566](https://github.com/intel/dokimi/issues/566) | Open issue | repos/workspace.py _guidance_at: on Windows two git names that decode alike share one key and the later blob replaces the earlier |
| [#573](https://github.com/intel/dokimi/issues/573) | Open issue | Rule ledger's Holm family counts rules whose changed cases cannot reach p < 0.05 |
| [#576](https://github.com/intel/dokimi/issues/576) | Open issue | evaluate --custom-questions <empty file> ends in a JSONDecodeError traceback, exit 1 |
| [#578](https://github.com/intel/dokimi/issues/578) | Open issue | Skip the script checks in skill routing that read only errors |
| [#579](https://github.com/intel/dokimi/issues/579) | Open issue | Cover the 22 uncovered changed lines of script_checks.py |
| [#580](https://github.com/intel/dokimi/issues/580) | Open issue | One install parser for online_checks and command_checks |
| [#581](https://github.com/intel/dokimi/issues/581) | Open issue | parse_json(contains=True) retries raw_decode from every bracket, so deeply nested input times out on Python 3.14 |
| [#582](https://github.com/intel/dokimi/issues/582) | Open issue | GitTree.is_file accepts a path that leaves the tree, so a pinned source and an unpinned one disagree on --manifest ../x |
| [#585](https://github.com/intel/dokimi/issues/585) | Open issue | Recompute the OpenAI generation deltas on the rows both judges graded |
| [#587](https://github.com/intel/dokimi/issues/587) | Open issue | Changelog fragment check fails every release PR that bumps the version |
| [#597](https://github.com/intel/dokimi/issues/597) | Open issue | schema_compat._nul_paths raises UnicodeDecodeError on a non-UTF-8 git path on Windows |
| [#598](https://github.com/intel/dokimi/issues/598) | Open issue | bootstrap_ci reports mean inf for finite data whose sum overflows |
| [#599](https://github.com/intel/dokimi/issues/599) | Open issue | paired_test forms deltas by plain subtraction: deltas beyond the float range give inf mean and nan sd |
| [#600](https://github.com/intel/dokimi/issues/600) | Open issue | point_biserial with an inf total returns r nan with reason None |
| [#601](https://github.com/intel/dokimi/issues/601) | Open issue | point_biserial reads tiny totals as no variance because of the absolute floor of 1.0 in _is_degenerate |
| [#602](https://github.com/intel/dokimi/issues/602) | Open issue | fixtures_digest raises UnicodeEncodeError on a fixture whose file name is not UTF-8 |
| [#604](https://github.com/intel/dokimi/pull/604) | Open PR | fix(evaluate): an unparseable --custom-questions file exits 2 |
| [#605](https://github.com/intel/dokimi/issues/605) | Open issue | eval score reference: an empty, non-JSON or non-UTF-8 --answers or --questions file ends in a traceback, exit 1 |
| [#606](https://github.com/intel/dokimi/pull/606) | Open PR | fix(repos): a ledger rule that cannot reach p < alpha stays out of the Holm family |
| [#607](https://github.com/intel/dokimi/pull/607) | Open PR | fix(eval): parse_json(contains=True) stops on deeply nested input |
| [#608](https://github.com/intel/dokimi/pull/608) | Open PR | fix(repo): a pinned source refuses a path that leaves the tree (#582) |
| [#609](https://github.com/intel/dokimi/issues/609) | Open issue | test: litellm 1.109.0 ReadOnly TypedDict notices fail the tag wheel tests |
| [#610](https://github.com/intel/dokimi/pull/610) | Open PR | test: ignore pydantic's ReadOnly TypedDict notice for any class |
| [#611](https://github.com/intel/dokimi/issues/611) | Open issue | scorecard._digest_ref raises UnicodeEncodeError on a skill file whose name is not UTF-8 |
| [#612](https://github.com/intel/dokimi/issues/612) | Open issue | harbor_export._content_key raises UnicodeEncodeError on a task file whose name is not UTF-8 |
| [#613](https://github.com/intel/dokimi/pull/613) | Open PR | fix: a git path or fixture name that is not UTF-8 no longer raises in schema_compat or fixtures_digest |
| [#614](https://github.com/intel/dokimi/issues/614) | Open issue | script scanner: count a case only at a command start |
| [#619](https://github.com/intel/dokimi/issues/619) | Open issue | parse_json(contains=True) costs starts x text length on a long text with many brackets |

## Engineering, dependencies and documentation

| Source | Kind at review | Work item |
|---|---|---|
| [#4](https://github.com/intel/dokimi/issues/4) | Open issue | Architecture cleanup: fix live bugs, then deepen the LLM, eval-record, novelty, arms and process modules |
| [#30](https://github.com/intel/dokimi/issues/30) | Open issue | Dependency Dashboard |
| [#61](https://github.com/intel/dokimi/issues/61) | Open issue | build: hash the dependency lock and install it with --require-hashes |
| [#228](https://github.com/intel/dokimi/issues/228) | Open issue | Answering stack: one implementation for evaluate/benchmark/answers and arms/subjects |
| [#229](https://github.com/intel/dokimi/issues/229) | Open issue | Consolidate duplicated subsystems (stats, LLM routing, judge loop, containers, digests, writers) |
| [#348](https://github.com/intel/dokimi/issues/348) | Open issue | Split repos/compare.py below the 800-line growth cap |
| [#383](https://github.com/intel/dokimi/issues/383) | Open issue | Drop the filelock override once a litellm release allows filelock 4 |
| [#385](https://github.com/intel/dokimi/issues/385) | Open issue | ci: shared gh-ci scripts: reject non-Linux matrix cells, CRLF METADATA, matrix.py parsing |
| [#430](https://github.com/intel/dokimi/issues/430) | Open issue | Drop the non-dict checks left by extract_json_object's dict-only return |
| [#492](https://github.com/intel/dokimi/pull/492) | Open PR | ci(deps): update non-major dependencies |
| [#568](https://github.com/intel/dokimi/pull/568) | Open PR | fix(records): four more answers/evaluations readers go through records.py |
| [#574](https://github.com/intel/dokimi/pull/574) | Open PR | fix(eval): a result that cannot reach p < 0.05 stays out of Holm's multiplier |
| [#615](https://github.com/intel/dokimi/pull/615) | Open PR | refactor(arms): CLI refusals take an ArmsRunConfig, not an argparse Namespace |
| [#616](https://github.com/intel/dokimi/pull/616) | Open PR | refactor(repos): drop the unused container re-exports of three docker_cmd constants |
| [#617](https://github.com/intel/dokimi/pull/617) | Open PR | refactor(repos): one module for the project-marker names |
| [#618](https://github.com/intel/dokimi/pull/618) | Open PR | refactor(stats): one resampler and one sign-flip row for the novelty and arms intervals |
| [#620](https://github.com/intel/dokimi/pull/620) | Open PR | refactor(novelty): parse the answer key's bundle record once |
| [#621](https://github.com/intel/dokimi/pull/621) | Open PR | refactor(judge): one default-judge rule that takes the answerer into account |
| [#622](https://github.com/intel/dokimi/pull/622) | Open PR | refactor(gate): one Check/Verdict type for every gate verdict |
| [#623](https://github.com/intel/dokimi/pull/623) | Open PR | refactor(docker): one run_spec builder for every docker run argv |
| [#625](https://github.com/intel/dokimi/pull/625) | Open PR | refactor(novelty): role clients, spend cap and USD estimate move to novelty/cost.py |
| [#627](https://github.com/intel/dokimi/pull/627) | Open PR | docs: organize Dokimi features into an illustrated guide |

This index makes omissions visible; it is not a promise that all work will ship. Update this dated inventory when refreshing the development guide, and keep runnable command documentation tied to merged functionality.

