<!--
A changelog fragment. Created with scriv; see changelog.d/README.md.
-->

### Changed

- **Eval reports are run artifacts** — `eval score grounding`, `eval score checklist`, `eval score pairwise`, `eval judge wording` and `eval judge agreement` now save their `--output` through the artifact writer, and `eval score ragas` and `answers generate --ragas` save `<answers>_ragas.json` the same way: each file carries `run_id` and `dokimi_version` after `schema_version`, is checked against its schema, and is written atomically, so `runs list` and `runs show RUN_ID` see them. The `schema_version` of checklist, pairwise and agreement reports moves from `*.v0` to `*.v1`, and their files now show as known kinds, not unknown versions. Files already written as `pairwise.v0`, `checklist.v0` or `cross_judge.v0` stay unknown to `runs`. A report its schema refuses is not written (kept as `<name>.rejected`) and the command exits 1.
