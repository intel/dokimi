<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

# Lightsage-inspired features for dokimi

Status: proposal + two prototypes (opportunities wired as `report opportunities`) on branch `claude/lightsage-feature-ideas-2aea58`.
Sources: Lightsage's public docs (`llms-full.txt`, 164 pages) and the JSON *shapes* (field names
only) of ~57 app API endpoints from a 2026-11-20 browser capture. No customer data, tokens or
response values are reproduced here.

## 1. What Lightsage is

A hosted "agent-led growth" platform for API/devtool vendors: it measures whether answer engines
and coding agents **find, recommend and successfully use** a product, then turns the gaps into a
daily ranked to-do list that the customer's own coding agent can pull over MCP/CLI and fix.

## 2. Feature map

| Module | What it does | Data model (from response shapes) | Exposed via |
|---|---|---|---|
| Visibility (prompts, topics, prompt runs) | Runs tracked prompts on scheduled answer engines and coding agents; mention rate, share of voice, citations | prompt{topic, platforms, tags, status}, run{mentions, citations}, targets{target_type, execution_platform, model_id, frequency, next_run_at} | UI, API v2, CLI, MCP |
| Agent Preference | Head-to-head: how often agents pick you over one named competitor | eval{competitor, prompts}, result{attempt, preferred} | UI, API |
| Custom evals (+ templates, configurations) | Coding-agent tasks × agents × runs (≤100 attempts) with judges, persona, CLIs, MCP servers, setup/cleanup commands, repo + environment | eval{prompts, judges, persona}, configuration{repository, environment, mcp, clis, skills}, template{id, version, expected_template_version} | UI, API, CLI (`evals run`), MCP |
| Eval runs | Durable queued job, polling, cancel, SSE monitor, sanitized trace pages | batch_job{status, progress, result}, dispatch{source: ui/cli/pr-bot/schedule} | API, CLI |
| CLI / MCP / Skills evals, plugin evals | Same eval engine scoped to an interface the vendor ships; plugin results per day as zip | per-plugin daily results | UI, API |
| API Performance / Devtool Arena | Public leaderboard: Eval score (completion) + Discovery score → 0-100, grade A-D; cost, tool calls, errors, time per run | competitor{score, grade, tool_calls, errors, docs_errors, total_time_ms, avg_*_per_eval} | public site, UI |
| Site audit, docs-map audit | Per-page agent-readiness score/grade/priority with checks passed/warned/failed and evidence | page{url, depth, sampled, score, grade, priority, checks_*}, sections | UI, API |
| Agent Analytics / Agent Tracking | Middleware/DNS integrations classify AI-agent visits to docs; sources, pages, error pages, attributed sign-ups | site, source, page, country rollups | middleware, UI, API |
| Library (pages, redirects) | Track a published page's citations and agent visits since go-live; does a redirect reach agents | page{went_live, first_citation}, redirect insights | UI, API |
| False claims, sentiment | What agents say about you that is wrong; quoted sentiment | findings/false-claims runs | UI |
| **Opportunities → Actionables / Tasks** | Daily ranked list: competitor gaps, quick wins, content gaps, technical fixes, on an impact × effort matrix; each item has an **execution prompt** | actionable{rank, batch_number, source_key, creation_mode, task_type, title, why, next_action, target_url}, task{status new/in_progress/done, priority, assignee} | UI, API, CLI, MCP, "pull actionables into your agent" prompt |
| Docs Agent (GitHub bot) | `/lightsage run evals` on a PR runs the docs evals against the PR's preview URL; only write+ users can trigger (credit protection) | dispatch source = PR bot | GitHub App |
| Agent onboarding | Ships its own Agent Skills, hosted MCP (OAuth/API key, 15+ client guides), CLI with `--output-format json`, llms.txt/llms-full.txt | memory-files (org/user preferences given to their agent) | docs |
| Billing/credits | Per-run credit cost by platform type; usage ledger per prompt/eval run | usage{job_type, credits_charged, customer_cost_usd, model} | UI |

## 3. Gap analysis vs dokimi

dokimi already covers the *measurement* core more rigorously than Lightsage: controlled A/B arms,
paired novelty, judge validity, routing/discovery, tool preference, repo claims, executable tasks,
baselines and CI. Lightsage is stronger on **closing the loop** and **distribution**.

| Lightsage idea | dokimi today | Fit | Value | Effort | Rank |
|---|---|---|---|---|---|
| Ranked actionables with execution prompts, stable keys, impact × effort | Findings live in separate reports (novelty, repo scan, catalog check); no cross-report queue | Strong: matches the "improvement loop" in the README | High | Low | **1 (prototyped)** |
| llms.txt / docs-site agent-readiness check | `repo scan` and `catalog check` lint repos and skills; nothing checks a docs site's agent index | Strong: free static check, "start with a free check" | Medium-high | Low | **2 (prototyped)** |
| PR slash-command evals (`/dokimi run`) with permission gate | `actions/` GitHub Action runs on CI; no comment trigger | Good: CI direction already exists | High | Medium | 3 |
| Composite score + letter grade (completion + discovery + cost/calls/errors/time) | Deltas with CIs, no single headline grade | Careful: a grade hides uncertainty; offer only alongside the CI | Medium | Low | 4 |
| Eval templates with versions and optimistic concurrency | Task/question sets by path in content repos | Partial: git already versions them | Low | Medium | 6 |
| "False claims" — what agents assert about your product that docs contradict | repo claims checks the docs, not the agent's answers | Good research direction: judge answers against the doc's atoms | Medium | Medium | 5 |
| Scheduled targets (prompt × agent × frequency) | Baselines + CI, no scheduler | Out of scope: dokimi is a tool, not a service | — | — | skip |
| Agent-visit analytics middleware, Library citations, sentiment, billing | — | Out of scope (hosted marketing analytics) | — | — | skip |

## 4. Experiments

### 4.1 `dokimi.opportunities` — one ranked queue across reports

[`dokimi/dokimi/opportunities.py`](../../dokimi/dokimi/opportunities.py), tests in
[`dokimi/tests/test_opportunities.py`](../../dokimi/tests/test_opportunities.py).

- Adapters read artifacts already on disk: `from_novelty` (paired `degraded` / `still_missing` /
  `redundant` counts; degraded weighted ×2 so a regression outranks a gap), `from_repo_static`
  (failing and unresolved claims grouped per file, with line numbers), `from_llms_txt`.
- Each `Opportunity` has impact (0-1, share of measured signal), effort, a quadrant
  (`act_now` / `plan` / `if_capacity` / `deprioritize`), a stable `source_key` (hash of kind,
  target, title — the same problem keeps its key across runs, as Lightsage's `source_key` does)
  and an `execution_prompt` that ends with "re-run the dokimi check that produced this finding",
  so the agent closes the loop with dokimi's own evidence rather than marking itself done.
- `rank` sorts quadrant → impact → effort; `render_markdown` gives a PR-comment table.

### 4.2 `dokimi.llms_txt` — static agent-index check

[`dokimi/dokimi/llms_txt.py`](../../dokimi/dokimi/llms_txt.py), tests in
[`dokimi/tests/test_llms_txt.py`](../../dokimi/tests/test_llms_txt.py). No network, no LLM.
Checks: one H1, blockquote summary, a "when to use" and a "when not to use" section (the
positioning Lightsage reports as most predictive), every listed link described, ~2,000-token budget.

### 4.3 Results

- 14 new tests pass; the import-architecture test passes with the two modules placed
  (`llms_txt` in components, `opportunities` in tracks); ruff, test-quality audit and duplicate
  check are clean. mypy and full `make ci-local` were **not** run (no dev venv in this worktree).
- Run on Lightsage's own public `llms.txt`: 7 findings — no blockquote summary, no when-to-use /
  when-not-to-use sections, three undescribed links, and ~9,200 tokens (4.6× the budget it
  recommends to customers). A cheap check catches real gaps even in a vendor of the practice.
- dokimi's root README read as an llms.txt ranks 3 `act_now` items (summary, when / when not).

## 5. Recommended next steps

1. Done: `dokimi report opportunities PATH... [--format markdown|json] [--top-n N] [--out FILE]`
   (inputs detected by content: novelty JSON, repo_static.v1 JSON, llms.txt; `opportunities.v1`
   schema). Still open: a `cli_output` envelope, a user-docs page and outcome test; adapters for
   `catalog check --json`, judge-validity and routing reports; per-atom novelty spans so the
   prompt names the exact passage.
2. Ship `dokimi docs llms-txt <path-or-file>` (or a `repo scan` metric) and a feature card under
   "Will the agent use it?"; later, optional fetch of `/llms.txt` + `robots.txt` AI-crawler rules.
3. Add a `/dokimi run` PR-comment trigger to the GitHub Action, gated on write permission, posting
   the `render_markdown` table — the Docs Agent pattern on dokimi's rigorous measurements.
4. Ship a dokimi Agent Skill + "pull opportunities and fix the top 3" workflow prompt (Lightsage's
   onboarding pattern), so a coding agent consumes the queue directly.
5. Research: "false claims" — grade agent answers against a doc's atoms to list wrong statements.
