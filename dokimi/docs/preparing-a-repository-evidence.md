<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

# Preparing a repository: the measurements behind the figures

This page records the runs behind each figure in
[preparing a repository for coding agents](preparing-a-repository.md). For each
one it gives the task set, the arms, the model, the cell count and the raw count.

The arms listed here (e.g., `off`, `tools`, `kit`, `rules`) were used to generate
the results on the main page. These arms are not part of the arms that `dokimi repo
run` knows today; only guidance arms (`guidance:none`, `guidance:raw`,
`guidance:bridge`) re-run today. To re-run a measurement, test it with the current
guidance arms as a hypothesis (see §8 on the main page).

How the figures were produced:

- **Repository and harness.** All runs used oneDAL at one fixed commit and
  Claude Code 2.1.286, except the Codex row of E6.
- **Model.** The model column is the ID the harness reported in its trace, not
  the alias it was asked for.
- **Cell.** A cell is one agent run of one task in one arm.
- **p-values.** A p-value is a two-sided sign test over cells paired by task and
  repetition. These are not `repo compare` output but computed directly from the
  cell results.
- **Sample size.** Samples are small, so read the counts, not the second decimal.
- **Raw data.** The per-cell results (`grade.json`, traces) are kept outside this
  repository. Every figure below was recomputed from them on 2026-10-19.

| ID | Figure on the page | Tasks | Arms | Model | Cells per arm | Result |
|---|---|---|---|---|---|---|
| <a id="e1"></a>E1 | §1 pinned formatter | 8 oneDAL fix and feature tasks: `fix_avx2_dispatch`, `fix_cel_thread_race`, `fix_df_extratrees_reg`, `fix_df_fraction_gt1`, `fix_finiteness_mut`, `fix_kmeans_csr_obj`, `fix_kmeans_empty_reloc`, `feat_xparam_cov_bias` | `off`; `fmtenv` (pinned clang-format on `PATH`, not named); `tools` (named `fmt` and `check` commands plus one `AGENTS.md` line) | Haiku 4.5 (`us.anthropic.claude-haiku-4-5-20251001-v1:0`) | 16 (8 tasks × 2) | CI format check passed: `off` 5/16, `fmtenv` 7/16, `tools` 12/16. `off`→`tools` 7 up and 0 down, p = 0.016. `fmtenv`→`tools` 5 up and 0 down, p = 0.062. `off`→`fmtenv` 2 up and 0 down, p = 0.5. Hidden tests passed: 11/16, 10/16, 12/16. |
| <a id="e1b"></a>E1b | §1, newer models | same 8 tasks | `off`, `tools` | Haiku 5.5 (`us.anthropic.claude-haiku-5-5`); Sonnet 5.5 (`us.anthropic.claude-sonnet-5-5`) | Haiku 16; Sonnet 8 | Format check: Haiku 5.5 12/16 → 12/14 (2 cells not graded for format); Sonnet 5.5 5/8 → 7/8. Hidden tests: Haiku 5.5 14/16 → 12/16; Sonnet 5.5 7/8 → 7/8. |
| <a id="e2"></a>E2 | §8 fast compile check | 3 renames of an ambiguous function: `edit_reset`, `edit_call_daal_kernel`, `edit_set_scale` (up to 34 callers; other functions share the name) | `off`, `tools` (as E1) | Haiku 4.5; Haiku 5.5; Sonnet 5.5 | 9 (3 tasks × 3) | Haiku 4.5: new failing translation units (all 1,589 compiled) 53 in `off` against 4 in `tools`, 5.9 → 0.4 per run. Pass 4/9 → 6/9. Haiku 5.5 and Sonnet 5.5: 9/9 in both arms, no new failing units. |
| <a id="e3"></a>E3 | §3 trap line | 4 reviews of a change that misses call sites: `review_allocate`, `review_call_daal_kernel`, `review_get_component_count`, `review_set_scale` | `off`; `kit` (navigation tool plus an `AGENTS.md` line saying that grep over-matches same-named functions) | Haiku 4.5 | 12 (4 tasks × 3) | Recall of the missed call sites 0.667 → 0.917 (3 cells up and 0 down, in 2 of 4 tasks, p = 0.25). The tool was called in 0 of 12 `kit` cells. |
| <a id="e4"></a>E4 | §2 and §5 | 12 "who calls X" questions with many callers or ambiguous names | none; tool only; tool plus guidance line | Haiku 5.5; Sonnet 5.5 | 24 (12 × 2) | F1 on caller-file set: Haiku `none` 0.874, `tool` 0.863, `tool+guidance` 0.951; Sonnet `none` 0.906, `tool` 0.894, `tool+guidance` 0.968. Cost per run: Haiku `none` $0.0121, `tool` $0.0115, `tool+guidance` $0.0081; Sonnet `none` $0.136, `tool` $0.142, `tool+guidance` $0.074. The tool was called in 2 and 1 of 24 `tool` runs, and in 13 and 16 of 24 `tool+guidance` runs (Haiku, Sonnet). |
| <a id="e5"></a>E5 | §5 fix and review tasks | 10 tasks: `review_replay_pr3580`, `pr3659`, `pr3665`, `pr3674`, `pr3773`; `review_seeded_fc`; `fix_df_extratrees_reg`, `fix_kmeans_csr_obj`, `fix_kmeans_empty_reloc`, `feat_xparam_cov_bias` | `off`; `kit` (tool plus guidance line) | Sonnet 5 (`claude-sonnet-5`) | 20 (10 × 2) | The tool was called in 0 of 20 `kit` cells. |
| <a id="e6"></a>E6 | §6 rules, seeded defects | 4 PRs with 13 seeded violations of recurring rules: `review_conv_daal_helper`, `review_conv_kmeans_helper`, `review_conv_make_rpath`, `review_conv_primitive_header` | `off`; `rules` (36 rules mined from past reviews, in the prompt); `lint` (clang-tidy and cppcheck findings on added lines, as hints) | Codex CLI with `gpt-6-luna`; Sonnet 5; Haiku 4.5; Haiku 5.5; Sonnet 5.5 | 8 (4 × 2) | Recall `off` / `rules` / `lint`: Codex 0.677 / 0.875 / 0.604 (`off`→`rules` 5 cells up and 0 down, in 3 of 4 tasks, p = 0.062). Sonnet 5 0.750 / 0.875 / 0.875. Haiku 4.5 0.781 / 0.812 / 0.844. Haiku 5.5 0.844 / 0.875 / 0.875. Sonnet 5.5 0.875 in every arm. The defects were drawn from the rule list, so these figures are an upper bound. |
| <a id="e7"></a>E7 | §6 rules, real PRs | the 5 `review_replay` tasks of E5 | `off`; `rules` in the prompt; `rulesmd` (rules in `AGENTS.md`); `lint` | Sonnet 5 (1 rep); Haiku 4.5 (2 reps) | 5; 10 | Recall of maintainer comments: Sonnet 5 0.19 / 0.21 / 0.21 / 0.22; Haiku 4.5 0.13 / 0.15 / 0.11 / 0.14. No effect. |
| <a id="e8"></a>E8 | §3 defects table | oneDAL `AGENTS.md` files against the tree | — | — | — | Corrected in [uxlfoundation/oneDAL#3869](https://github.com/uxlfoundation/oneDAL/pull/3869): no pkg-config generator script exists; 26 of 31 algorithms have a CPU backend and 20 of 31 a GPU backend; 46 backend files convert the older API's error codes by design. |

Figures that no stored result backs are given on the page without a number:

- the share of a function's callers that a CPU-only compile database missed (§4);
- the agreement between indexers (§4).
