# False claims: agent answers that contradict the product's documentation

Status: research prototype, not wired into any command, score or gate.
Code: `dokimi/dokimi/novelty/false_claims.py`. Tests: `dokimi/tests/test_novelty_false_claims.py`.

## Why

Commercial "AI visibility" tools report the *false claims* assistants make about a product
("ChatGPT says X needs driver 3.0; the docs say 2.1"). dokimi already checks what a
document asserts (atoms, with span provenance and a faithfulness check) and how well a
model answers *questions derived from* the document (novelty `measure`). It does not take
an answer the agent gave to an arbitrary user question and list which documented facts the
answer contradicts. This note describes that check and a first offline prototype of it.

## Method

Inputs are stored, so no model is called unless a judge is passed in:

* the product's atoms (`load_atoms` record: statement, span ids, faithfulness);
* one stored answer text (from an eval transcript, an arm record, or pasted).

Steps:

1. **Split** the answer into sentences with character offsets. A trailing
   `confidence: x` line (the novelty answerer's format) is not a claim. Decimal points
   (`3.0`) and dotted names do not end a sentence.
2. **Pair** each sentence with the atoms it is plausibly about, lexically: a shared
   distinctive token (flag, `snake_case`, `CamelCase`, dotted identifier), or at least 30%
   of the atom's content words. Flags and identifiers are split into parts, so
   `--use-accel` still pairs with an atom about `--enable-accel`; numbers are not used to
   pair, because they are what a contradiction changes. Atoms whose faithfulness check
   came back `distorted` or `unsupported` are not ground truth and are skipped.
3. **Verdict** per pair:
   * *without a judge*: a lexical signal only, labelled `lexical:value` (both sides name a
     value of the same shape — version/number, or flag — and share none) or
     `lexical:polarity` (exactly one side is negated). These are candidates.
   * *with a judge* (any `prompt -> {"verdict","quote"}`, e.g. `LLMClient.json_object`):
     `contradicts` / `supports` / `unrelated` plus a quote. A `contradicts` whose quote is
     not a verbatim substring of the sentence is counted as `n_unverified_quote` and not
     reported. A failed call or an off-scale verdict is `n_judge_errors`, never "clean".
4. **Report** each flagged claim with the atom id, statement, span ids (so it can be
   rendered on the doc like other novelty output), the answer sentence and the quote,
   plus counts: sentences, `n_unchecked` (sentences no atom paired with), pairs, unverified
   quotes, judge errors.

## How it differs from the existing judges

| | novelty `measure` `_grade` | repo/claims checks | false claims (this) |
|---|---|---|---|
| question | the atom's own probe question | — | any user question |
| graded text | one reply to that probe | the doc itself | every sentence of a free answer |
| reference | the atom's expected answer | the code/repo | all paired atoms |
| output | 6-way verdict per probe | doc claim vs. repo | contradicted atoms + quotes |
| silence | `abstained` | — | `unchecked` (no atom covers it) |

`measure` asks "does the model know this atom?"; this asks "of what the agent *chose* to
say, what does the documentation contradict?". It reuses atoms (and their faithfulness
labels and span provenance) unchanged; the judge prompt is new and narrower (direct
conflict only — omission and vagueness are not contradictions, unlike `wrong_other`,
which also covers "irrelevant").

## Validity risks

* **Coverage, not truth.** A sentence no atom pairs with is unchecked, not true. The
  report must show `n_unchecked / n_sentences` next to any count of false claims; a low
  false-claim count over a mostly-unchecked answer means nothing.
* **Pairing recall.** Lexical pairing misses paraphrases ("needs the 3.x driver" vs
  "requires driver version 2.1") and synonyms. An embedding pre-filter would raise recall;
  measure it against a hand-labelled set before trusting any rate.
* **Lexical precision is low.** Different numbers in one sentence can be about different
  things (a port and a version); a negation can sit in another clause. Lexical hits are
  triage for a judge or a human, never a published rate.
* **Stale or scoped docs.** An atom true for v2 contradicts a correct answer about v3.
  `version_bound` / `temporal` atoms need the answer's version context; until then flag
  them for review instead of counting them.
* **Wrong atoms.** Extraction errors become false accusations. Only `supported`/`unchecked`
  atoms are used; a production version should require `supported`.
* **Judge bias.** The judge should not be the model that wrote the answer (reuse
  `check_judge2`'s `same_client` rule), and its agreement with a second judge on a sample
  should be reported, as `judge2` does for `measure`.
* **Quote check is necessary, not sufficient.** A verbatim quote proves the words exist,
  not that they conflict.

## Cost

Pairing is local. With a judge: one short call per (sentence, paired atom) pair —
roughly 150–250 input tokens and ~30 output tokens each. A 10-sentence answer against a
few hundred atoms typically yields 5–30 pairs, i.e. under 10k tokens per answer; a 200-answer
corpus is in the low millions of tokens, so a small model is affordable and a spend cap
(`SpendCap`) should bound it. Batching all pairs of one sentence into one call would cut
the call count further. No paid calls were made for this prototype.

## Next steps

1. Hand-label ~50 stored answers (from existing arm records) for contradictions; measure
   pairing recall and judge precision/recall on them.
2. Add an embedding pre-filter if pairing recall is the bottleneck.
3. Version-aware handling of `version_bound` atoms.
4. Only then: a CLI (`skill novelty false-claims --atoms … --answers …`) writing a
   `false_claims.v1` artifact, with the coverage figure beside every count.
