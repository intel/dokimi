# Review: "eval grounding: baseline lift, --min-contexts, --annotate"

Blocking issues first.

1. **BLOCKING: `dokimi/dokimi/schemas/answers.v1.json` lines 37 and 67 make the schema stricter in place.**
   The commit adds `grounding` to the `required` list of every `answers[]` item in
   `answers.v1.json`. Every answers.v1 artifact already on disk was written without that
   field, so after this change they all fail validation (`load_artifact`, dataset export).
   The repo allows an in-place edit only when it widens the schema. A new required field is
   a breaking change, so it needs a new version: add `answers.v2.json`, point
   `artifacts.SCHEMA_VERSIONS["answers"]` at it, and list `answers.v1` in
   `SUPERSEDED_VERSIONS`. The other option is to make `grounding` optional, which only widens v1.
   CI's `schema_compat` job fails on this commit: "BREAKING answers.v1.json /answers[]:
   'grounding' is now required". Editing the base fixture
   `tests/fixtures/artifacts/answers.v1.json` to add `grounding` hides the break from
   local tests but does not fix it.

2. **BLOCKING: `dokimi/pyproject.toml` line 109 adds `dokimi.eval.grounding` to the
   `[[tool.mypy.overrides]]` `ignore_errors` list.**
   Repo policy is that this exemption list only shrinks. The type errors it hides are real
   and come from the new code: `score: float = None` and `base: float = None` in
   `annotate_answers` (`grounding.py` lines 106-107, plus line 111). Fix them with
   `float | None` annotations and leave the module type-checked. Do not add it to the
   ignore list.

3. **BLOCKING: `dokimi/dokimi/eval/grounding.py` lines 117-118 rename persisted keys.**
   `annotate_answers` writes the answer conditions as `with_context` and `baseline` instead
   of `with_docs` and `without_docs`. It does this in an answers.v1 artifact written by
   `save_artifact("answers", ...)` in `commands/evaluate.py` line 254. Every stored run and
   every reader (`eval score`, reports, `_contexts_of` itself) uses the `with_docs` and
   `without_docs` keys. Running `eval score` or `eval grounding` again on the annotated file
   finds no answers. Keep the original key names. The code comment "the arm names the rest
   of the pipeline uses" is wrong.

4. **BLOCKING (bug): `dokimi/dokimi/eval/grounding.py` line 108 has an off-by-one in
   `annotate_answers`.**
   The condition is `len(contexts) > min_contexts`. It should be `>=`, which matches
   `evaluate_grounding` (line 71: skip when `< min_contexts`) and the help text "fewer than
   N". With the default `--min-contexts 1`, every answer that retrieved exactly one snippet
   gets `score`, `baseline_score` and `lift` set to null in the annotated file, even though
   the report counts it. The two code paths disagree about the same flag. The new test only
   uses 2 contexts with min 1, so it misses this boundary case.

5. **BLOCKING (docs drift): `dokimi/docs/benchmarking-and-comparison.md` lines 201 and 208
   name a flag that does not exist.**
   The docs use `--min-docs` (and `--min-docs 2` in the example command), but the flag this
   commit adds in `commands/evaluate.py` line 461 is `--min-contexts`. The documented
   command fails with "unrecognized arguments". `tests/test_docs_code_refs.py` fails on it
   ("unknown flag: eval grounding --min-docs"). Rename the flag in the docs to match the code.

Non-blocking:

- `annotate_answers` rebuilds each item from a fixed set of fields, so it drops any other
  per-answer fields that were in the input file.
- The grounding report does not record `min_contexts` in its `summary`, so a reader cannot
  tell which filter was applied.

Verdict: request changes. Items 1-5 block the merge.
