### Added

- **`catalog trigger-probe`** — runs each trigger-set query in a fresh, unannounced
  `claude -p` session with the catalog committed as project skills, a scratch `HOME`
  and config, and the query sent verbatim; scores the `Skill` calls with the same
  selection summary as the `skill-catalog:` arm, one column per trial.
- **`dokimi.harnesses.payload_audit`** — `audit_payload` / `new_leaks` report dokimi
  artifacts (arm names, label keys, question ids, grading words, task paths) in a
  captured request, argv or env, with operator content blanked and the stock harness's
  own text subtracted.
